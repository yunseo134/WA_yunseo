from client.ui.dpi import configure_high_dpi

if __name__ == "__main__":
    configure_high_dpi()
    from client.ui.main_window import App

    app = App()
    app.start()
