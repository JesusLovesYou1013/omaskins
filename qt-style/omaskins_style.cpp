// OmaSkins' Qt style: Qt's own Fusion look, with see-through app windows.
//
// The colours come from the palette OmaSkins writes (~/.config/omaskins/qt-palette.conf, from the current
// Omarchy theme and the Transparency step); the style watches it, so open apps follow a theme switch or a
// Transparency change within a moment. The window background is painted from the palette's Window colour,
// transparency included. Menu bars and toolbars let that background show through; pop-up menus stay solid
// (easy to read). Each app with a see-through window leaves a marker ($XDG_RUNTIME_DIR/omaskins-qt/<pid>)
// so OmaSkins keeps Hyprland's whole-window fade off it (a video inside must stay solid).
// Only public Qt API: built on each machine by OmaSkins, no rebuild needed for normal Qt updates.

#include <QApplication>
#include <QDir>
#include <QEvent>
#include <QFile>
#include <QFileInfo>
#include <QFileSystemWatcher>
#include <QIcon>
#include <QSettings>
#include <QTimer>
#include <QPaintEvent>
#include <QDialog>
#include <QFrame>
#include <QMainWindow>
#include <QPainter>
#include <QProxyStyle>
#include <QStyleFactory>
#include <QStyleOptionMenuItem>
#include <QStylePlugin>
#include <QWidget>

// OmaSkins' palette file: Qt's 21 colour roles per group (qt5ct's colour scheme layout) and the icon theme.
static QString paletteFile() {
    QString config = qEnvironmentVariable("XDG_CONFIG_HOME");
    if (config.isEmpty())
        config = QDir::homePath() + QStringLiteral("/.config");
    return config + QStringLiteral("/omaskins/qt-palette.conf");
}

static bool readPalette(QPalette *pal, QString *icons) {
    if (!QFile::exists(paletteFile()))
        return false;
    QSettings f(paletteFile(), QSettings::IniFormat);
    const struct { const char *key; QPalette::ColorGroup group; } groups[] = {
        {"ColorScheme/active_colors", QPalette::Active},
        {"ColorScheme/inactive_colors", QPalette::Inactive},
        {"ColorScheme/disabled_colors", QPalette::Disabled}};
    for (const auto &g : groups) {
        const QStringList colours = f.value(QLatin1String(g.key)).toStringList();
        if (colours.size() < QPalette::NColorRoles)
            return false;   // half-written or not ours: keep what we have
        for (int role = 0; role < QPalette::NColorRoles; ++role)
            pal->setColor(g.group, QPalette::ColorRole(role), QColor(colours.at(role).trimmed()));
    }
    *icons = f.value(QStringLiteral("Icons/theme")).toString();
    return true;
}

// Keeps the app's colours in step with the palette file (OmaSkins replaces it whole, so the folder is
// watched, not the file).
class PaletteFollower : public QObject {
public:
    static PaletteFollower *instance() {
        static PaletteFollower *one = new PaletteFollower(qApp);
        return one;
    }

private:
    explicit PaletteFollower(QObject *parent) : QObject(parent) {
        timer.setSingleShot(true);
        timer.setInterval(150);   // let a burst of writes settle
        connect(&timer, &QTimer::timeout, this, &PaletteFollower::apply);
        const QString dir = QFileInfo(paletteFile()).absolutePath();
        QDir().mkpath(dir);
        watcher.addPath(dir);
        connect(&watcher, &QFileSystemWatcher::directoryChanged, &timer, qOverload<>(&QTimer::start));
        timer.start();
    }

    void apply() {
        QPalette pal;
        QString icons;
        if (!readPalette(&pal, &icons))
            return;
        if (pal != QApplication::palette())
            QApplication::setPalette(pal);
        if (!icons.isEmpty() && icons != QIcon::themeName())
            QIcon::setThemeName(icons);
    }

    QFileSystemWatcher watcher;
    QTimer timer;
};

// Tells OmaSkins this app has see-through windows: Hyprland's whole-window fade then stays off it.
static void markSeeThroughApp() {
    static bool done = false;
    if (done)
        return;
    done = true;
    const QString runtime = qEnvironmentVariable("XDG_RUNTIME_DIR");
    if (runtime.isEmpty())
        return;
    const QString dir = runtime + QStringLiteral("/omaskins-qt");
    QDir().mkpath(dir);
    QFile mark(dir + QLatin1Char('/') + QString::number(QCoreApplication::applicationPid()));
    mark.open(QIODevice::WriteOnly);
}

// Paints see-through windows' background from the palette. It belongs to the app, not to the style: if an
// app swaps in another style, a see-through window left with nobody painting its background would go fully
// clear.
class BackgroundPainter : public QObject {
public:
    static BackgroundPainter *instance() {
        static BackgroundPainter *one = new BackgroundPainter(qApp);
        return one;
    }

    bool eventFilter(QObject *o, QEvent *e) override {
        if (e->type() == QEvent::Paint) {
            auto *w = static_cast<QWidget *>(o);
            QPainter p(w);
            p.setCompositionMode(QPainter::CompositionMode_Source);
            p.fillRect(static_cast<QPaintEvent *>(e)->rect(), w->palette().color(QPalette::Window));
        }
        return false;
    }

private:
    using QObject::QObject;
};

class OmaSkinsStyle : public QProxyStyle {
    Q_OBJECT
public:
    OmaSkinsStyle() : QProxyStyle(QStyleFactory::create(QStringLiteral("Fusion"))) {}

    // The theme's colours from the start (Omarchy's gtk3 platform theme gives Qt5 apps none of its own).
    QPalette standardPalette() const override {
        QPalette pal = QProxyStyle::standardPalette();
        QString icons;
        readPalette(&pal, &icons);
        return pal;
    }

    using QProxyStyle::polish;   // keep the palette overload visible

    void polish(QApplication *app) override {
        QProxyStyle::polish(app);
        PaletteFollower::instance();
    }

    void polish(QWidget *w) override {
        QProxyStyle::polish(w);
        makeSeeThrough(w);
    }

    // Qt often styles windows only AFTER their native window exists, when it's too late to make them
    // see-through. Qt asks the style for hints while a window is still being set up, before that: so we do
    // it from there too (Kvantum's way, with Kvantum's safety checks).
    int styleHint(StyleHint hint, const QStyleOption *opt, const QWidget *w, QStyleHintReturn *r) const override {
        makeSeeThrough(const_cast<QWidget *>(w));
        return QProxyStyle::styleHint(hint, opt, w, r);
    }

    void makeSeeThrough(QWidget *w) const {
        if (!w || !w->isWindow() || w->testAttribute(Qt::WA_TranslucentBackground)
                || w->testAttribute(Qt::WA_WState_Created) || w->windowHandle()   // too late for this one
                || w->testAttribute(Qt::WA_NoSystemBackground) || w->autoFillBackground()
                || w->testAttribute(Qt::WA_PaintOnScreen))
            return;
        const auto type = w->windowFlags() & Qt::WindowType_Mask;
        if (type != Qt::Window && type != Qt::Dialog)
            return;
        // Buttons, sliders, ... count as windows too while they're being built (no parent yet): only real
        // main windows and dialogs.
        if (!qobject_cast<QMainWindow *>(w) && !qobject_cast<QDialog *>(w))
            return;
        if (w->windowFlags() & (Qt::FramelessWindowHint | Qt::X11BypassWindowManagerHint)
                || qobject_cast<QFrame *>(w) || w->inherits("QSplashScreen"))
            return;
        if (auto *mw = qobject_cast<QMainWindow *>(w)) {
            if (w->parentWidget())
                return;   // a main window inside another one
            if (QWidget *cw = mw->centralWidget(); cw && cw->autoFillBackground())
                return;   // its middle paints its own background
        }
        w->setAttribute(Qt::WA_TranslucentBackground);   // sets the alpha buffer safely, this early
        w->installEventFilter(BackgroundPainter::instance());
        markSeeThroughApp();
    }


    void drawPrimitive(PrimitiveElement pe, const QStyleOption *opt, QPainter *p, const QWidget *w) const override {
        switch (pe) {
        case PE_PanelMenuBar:
        case PE_PanelToolBar:
            return;   // the window's own background shows through
        case PE_PanelMenu:
        case PE_FrameMenu: {
            QColor c = opt->palette.color(QPalette::Window);
            c.setAlpha(255);   // pop-up menus solid
            p->fillRect(opt->rect, c);
            if (pe == PE_FrameMenu)
                QProxyStyle::drawPrimitive(pe, opt, p, w);
            return;
        }
        default:
            break;
        }
        QProxyStyle::drawPrimitive(pe, opt, p, w);
    }

    void drawControl(ControlElement ce, const QStyleOption *opt, QPainter *p, const QWidget *w) const override {
        if (ce == CE_MenuBarEmptyArea || ce == CE_ToolBar)
            return;   // window background shows through
        if (ce == CE_MenuBarItem) {
            if (auto *item = qstyleoption_cast<const QStyleOptionMenuItem *>(opt)) {
                QStyleOptionMenuItem o(*item);
                o.palette.setBrush(QPalette::Window, Qt::transparent);   // no second layer behind each item
                QProxyStyle::drawControl(ce, &o, p, w);
                return;
            }
        }
        QProxyStyle::drawControl(ce, opt, p, w);
    }
};

class OmaSkinsStylePlugin : public QStylePlugin {
    Q_OBJECT
    Q_PLUGIN_METADATA(IID QStyleFactoryInterface_iid FILE "omaskins.json")
public:
    QStyle *create(const QString &key) override {
        return key.compare(QStringLiteral("OmaSkins"), Qt::CaseInsensitive) == 0 ? new OmaSkinsStyle : nullptr;
    }
};

#include "omaskins_style.moc"
