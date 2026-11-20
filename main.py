import sys

if __name__ == "__main__":
    # The terminal front ends don't import Qt, so they also run where
    # PySide6 isn't installed (see requirements-cli.txt).
    if any(arg in ("--tui", "--headless", "--list-devices") for arg in sys.argv[1:]):
        from meshhunter.tui.app import main

        sys.exit(main())
    from meshhunter.gui_qt.app import main

    main()
