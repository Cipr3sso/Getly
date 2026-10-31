using Microsoft.UI.Xaml;

namespace Getly.UI;

public partial class App : Application
{
    private Window? _window;

    public App()
    {
        InitializeComponent();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        _window = new Views.MainWindow();
        _window.Activate();
    }
}
