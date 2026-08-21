"""bmesh topology helpers for Re-circle.

Selection walking (closed loops *and* open arcs), boundary detection after a
face strip is removed, and normal orientation for freshly built faces.
Everything that knows about bmesh but not about operators lives here.
"""

from collections import defaultdict, deque


# --------------------------------------------------------------- basic lookups

def edge_between(a, b):
    """The edge joining verts `a` and `b`, or None."""
    for e in a.link_edges:
        if e.other_vert(a) is b:
            return e
    return None


def cycle_edges(loop):
    """Edges of a closed, ordered vertex loop."""
    edges = []
    n = len(loop)
    for i in range(n):
        e = edge_between(loop[i], loop[(i + 1) % n])
        if e is not None:
            edges.append(e)
    return edges


def chain_edges(chain):
    """Edges of an open, ordered vertex chain."""
    edges = []
    for i in range(len(chain) - 1):
        e = edge_between(chain[i], chain[i + 1])
        if e is not None:
            edges.append(e)
    return edges


def curve_edges(verts, closed):
    """Edges of an ordered vertex run, closed or open."""
    return cycle_edges(verts) if closed else chain_edges(verts)


# ------------------------------------------------------- component ordering

def _order_cycle(comp, adj, start):
    """Walk a 2-regular component into an ordered vertex list, or None."""
    ordered = [start]
    prev, cur = None, start
    while True:
        nxts = [n for n in adj[cur] if n is not prev]
        if not nxts:
            return None
        nxt = nxts[0]
        if nxt is start:
            break
        ordered.append(nxt)
        prev, cur = cur, nxt
        if len(ordered) > len(comp):
            return None
    return ordered if len(ordered) == len(comp) else None


def _order_chain(comp, adj, end):
    """Walk an open path component from endpoint `end`, or None."""
    ordered = [end]
    prev, cur = None, end
    while True:
        nxts = [n for n in adj[cur] if n is not prev]
        if not nxts:
            break
        nxt = nxts[0]
        ordered.append(nxt)
        prev, cur = cur, nxt
        if len(ordered) > len(comp):
            return None
    return ordered if len(ordered) == len(comp) else None


def ordered_components(adj):
    """Split a vertex adjacency map into ordered cycles and open chains.

    Returns (cycles, chains, n_bad):
      * cycles — every vertex has exactly two neighbours and the walk closes,
      * chains — exactly two endpoints (one neighbour), the rest have two,
      * n_bad  — components that branch (a vertex with three+ neighbours) or
        otherwise fail to order.
    """
    cycles, chains, n_bad, seen = [], [], 0, set()
    for s in list(adj):
        if s in seen:
            continue
        comp, stack = set(), [s]
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x)
            for y in adj[x]:
                if y not in comp:
                    stack.append(y)
        seen |= comp

        degrees = {v: len(adj[v]) for v in comp}
        if any(d > 2 or d == 0 for d in degrees.values()):
            n_bad += 1
            continue

        ends = [v for v, d in degrees.items() if d == 1]
        if not ends:
            ordered = _order_cycle(comp, adj, s)
            if ordered is None:
                n_bad += 1
            else:
                cycles.append(ordered)
        elif len(ends) == 2:
            ordered = _order_chain(comp, adj, ends[0])
            if ordered is None:
                n_bad += 1
            else:
                chains.append(ordered)
        else:
            n_bad += 1
    return cycles, chains, n_bad


def _cycles_from_adjacency(adj):
    """Backwards-compatible view: cycles only, chains counted as bad."""
    cycles, chains, n_bad = ordered_components(adj)
    return cycles, n_bad + len(chains)


def _selection_adjacency(bm):
    adj = defaultdict(list)
    for e in bm.edges:
        if e.select:
            a, b = e.verts
            adj[a].append(b)
            adj[b].append(a)
    return adj


def selected_curves(bm):
    """Ordered closed loops and open arcs formed by the selected edges.

    Returns (cycles, chains, n_bad).
    """
    return ordered_components(_selection_adjacency(bm))


def selected_cycles(bm):
    """Ordered closed loops formed by the currently selected edges."""
    return _cycles_from_adjacency(_selection_adjacency(bm))


def boundary_cycles(verts):
    """Ordered closed loops among `verts`, following boundary/wire edges only.

    An edge counts as boundary when it has fewer than two faces — exactly the
    edges exposed after the loop's face strips are removed.
    """
    cycles, _ = _cycles_from_adjacency(_boundary_adjacency(verts))
    return cycles


def _boundary_adjacency(verts):
    vset = set(v for v in verts if v.is_valid)
    adj = defaultdict(list)
    for v in vset:
        for e in v.link_edges:
            if len(e.link_faces) >= 2:
                continue
            ov = e.other_vert(v)
            if ov in vset:
                adj[v].append(ov)
    return adj


def boundary_chains(verts):
    """Ordered *open* runs among `verts`, following boundary/wire edges only.

    The arc counterpart of `boundary_cycles`: after an arc's face strip is
    deleted, the neighbouring vertices are left as an open polyline, not a ring.
    Vertices with no boundary neighbour at all are simply not part of any chain
    (a fan centre, say) — the caller deals with those separately.
    """
    _, chains, _ = ordered_components(_boundary_adjacency(verts))
    return chains


# ------------------------------------------------------------ face orientation

def _edge_dir(face, a, b):
    """+1 if `face` traverses a->b, -1 if b->a, 0 if a,b aren't a face edge."""
    vs = face.verts[:]
    n = len(vs)
    for k in range(n):
        if vs[k] is a and vs[(k + 1) % n] is b:
            return 1
        if vs[k] is b and vs[(k + 1) % n] is a:
            return -1
    return 0


def orient_new_faces(new_faces):
    """Make freshly built faces agree with each other and with existing geometry.

    Walks each connected component of `new_faces`, deciding which faces to flip
    so they stay consistent across shared edges, and anchoring the component to
    an adjacent pre-existing face when one exists. Components with no existing
    neighbour are returned so the caller can fall back to recalc.

    Crucially, all `normal_flip()` calls happen *after* the graph walk. Flipping
    a face rewrites the radial cycle of its edges, so flipping mid-iteration of
    an `edge.link_faces` walk corrupts that iterator into an infinite loop; we
    decide everything against an up-front winding snapshot instead.
    """
    new_set = set(new_faces)
    # Snapshot winding so edge-direction lookups stay stable while we plan flips.
    order = {f: f.verts[:] for f in new_set}

    def edir(f, a, b):
        vs = order[f]
        n = len(vs)
        for k in range(n):
            if vs[k] is a and vs[(k + 1) % n] is b:
                return 1
            if vs[k] is b and vs[(k + 1) % n] is a:
                return -1
        return 0

    flip = {}          # face -> should it be flipped (relative to its component)
    visited = set()
    unseeded = []

    for start in new_set:
        if start in visited:
            continue
        visited.add(start)
        flip[start] = False
        comp = [start]
        queue = deque([start])
        while queue:
            f = queue.popleft()
            for e in f.edges:
                a, b = e.verts
                for g in list(e.link_faces):
                    if g is f or g not in new_set or g in visited:
                        continue
                    visited.add(g)
                    # Two faces are consistent iff they traverse the shared edge
                    # in opposite directions, accounting for f's planned flip.
                    f_eff = edir(f, a, b) * (-1 if flip[f] else 1)
                    flip[g] = (edir(g, a, b) == f_eff)
                    comp.append(g)
                    queue.append(g)

        # Anchor the whole component to any adjacent pre-existing face.
        anchor_flip = None
        for f in comp:
            if anchor_flip is not None:
                break
            for e in f.edges:
                a, b = e.verts
                found = False
                for g in e.link_faces:
                    if g in new_set or not g.is_valid:
                        continue
                    g_dir = _edge_dir(g, a, b)
                    if g_dir == 0:
                        continue
                    f_eff = edir(f, a, b) * (-1 if flip[f] else 1)
                    # Same direction across the edge means the component is
                    # wound backwards relative to the existing surface.
                    anchor_flip = (f_eff == g_dir)
                    found = True
                    break
                if found:
                    break
        if anchor_flip is None:
            unseeded.extend(comp)
        elif anchor_flip:
            for f in comp:
                flip[f] = not flip[f]

    # Apply all flips now that no bmesh iterator is live.
    for f, do in flip.items():
        if do and f.is_valid:
            f.normal_flip()
    return unseeded
