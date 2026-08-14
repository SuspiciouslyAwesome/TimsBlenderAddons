if "main" in locals():
    import importlib
    importlib.reload(main)
else:
    from . import main


def register():
    main.register()


def unregister():
    main.unregister()
