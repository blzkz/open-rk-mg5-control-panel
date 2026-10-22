import sys

if "--probe" in sys.argv:
    from .probe import main
else:
    from .app import main

sys.exit(main())
