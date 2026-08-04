#include "clavis_paths.h"

#include <QDir>
#include <QFileInfo>

namespace Clavis::Runtime {
namespace {

QString env(const char *name)
{
    return QString::fromLocal8Bit(qgetenv(name)).trimmed();
}

QString prependPath(const QString &path, const QString &existing)
{
    if (existing.isEmpty())
        return path;
    const QStringList entries = existing.split(QLatin1Char(':'), Qt::SkipEmptyParts);
    if (entries.contains(path))
        return existing;
    return path + QLatin1Char(':') + existing;
}

} // namespace

ClavisPaths ClavisPaths::fromEnvironment()
{
    ClavisPaths paths;
    paths.m_home = cleanAbsolute(env("HOME"));
    if (paths.m_home.isEmpty())
        paths.m_home = cleanAbsolute(QDir::homePath());

    paths.m_binHome = cleanAbsolute(env("CLAVIS_BIN_HOME"));
    if (paths.m_binHome.isEmpty())
        paths.m_binHome = paths.m_home + QStringLiteral("/.local/bin");

    paths.m_stableKey = cleanAbsolute(env("CLAVIS_KEY"));
    if (paths.m_stableKey.isEmpty()) {
        paths.m_stableKey = QDir(paths.m_binHome).filePath(
            QStringLiteral("key"));
    }

    paths.m_installPrefix = cleanAbsolute(env("CLAVIS_INSTALL_PREFIX"));
    if (paths.m_installPrefix.isEmpty())
        paths.m_installPrefix = paths.m_home + QStringLiteral("/.local/lib/clavis");

    paths.m_configHome = environmentPath(
        "CLAVIS_CONFIG_HOME", "XDG_CONFIG_HOME",
        paths.m_home + QStringLiteral("/.config"), QStringLiteral("clavis"));
    paths.m_dataHome = environmentPath(
        "CLAVIS_DATA_HOME", "XDG_DATA_HOME",
        paths.m_home + QStringLiteral("/.local/share"), QStringLiteral("clavis"));
    paths.m_stateHome = environmentPath(
        "CLAVIS_STATE_HOME", "XDG_STATE_HOME",
        paths.m_home + QStringLiteral("/.local/state"), QStringLiteral("clavis"));
    paths.m_cacheHome = environmentPath(
        "CLAVIS_CACHE_HOME", "XDG_CACHE_HOME",
        paths.m_home + QStringLiteral("/.cache"), QStringLiteral("clavis"));

    paths.m_runtimeHome = cleanAbsolute(env("CLAVIS_RUNTIME_HOME"));
    if (paths.m_runtimeHome.isEmpty()) {
        QString runtimeBase = cleanAbsolute(env("XDG_RUNTIME_DIR"));
        if (runtimeBase.isEmpty())
            runtimeBase = paths.m_cacheHome + QStringLiteral("/runtime");
        paths.m_runtimeHome = QDir(runtimeBase).filePath(QStringLiteral("clavis"));
    }

    paths.m_profileName = env("CLAVIS_PROFILE");
    if (paths.m_profileName.isEmpty()
        || paths.m_profileName.contains(QLatin1Char('/'))
        || paths.m_profileName.contains(QLatin1Char('\\'))
        || paths.m_profileName == QStringLiteral(".")
        || paths.m_profileName == QStringLiteral("..")) {
        paths.m_profileName = QStringLiteral("default");
    }
    paths.m_profileHome = cleanAbsolute(env("CLAVIS_PROFILE_HOME"));
    if (paths.m_profileHome.isEmpty()) {
        paths.m_profileHome = QDir(paths.m_dataHome).filePath(
            QStringLiteral("profiles/%1").arg(paths.m_profileName));
    }
    paths.m_profileConfigHome = cleanAbsolute(env("CLAVIS_PROFILE_CONFIG_HOME"));
    if (paths.m_profileConfigHome.isEmpty()) {
        paths.m_profileConfigHome = QDir(paths.m_configHome).filePath(
            QStringLiteral("profiles/%1").arg(paths.m_profileName));
    }
    paths.m_generatedHome = cleanAbsolute(env("CLAVIS_GENERATED_HOME"));
    if (paths.m_generatedHome.isEmpty()) {
        paths.m_generatedHome = QDir(paths.m_profileHome).filePath(
            QStringLiteral("generated"));
    }
    paths.m_qmlImportHome = cleanAbsolute(env("CLAVIS_QML_IMPORT_HOME"));
    if (paths.m_qmlImportHome.isEmpty()) {
        paths.m_qmlImportHome = QDir(paths.currentRelease()).filePath(
            QStringLiteral("lib/qml"));
    }
    return paths;
}

QString ClavisPaths::home() const { return m_home; }
QString ClavisPaths::binHome() const { return m_binHome; }
QString ClavisPaths::installPrefix() const { return m_installPrefix; }
QString ClavisPaths::releasesHome() const
{
    return QDir(m_installPrefix).filePath(QStringLiteral("releases"));
}
QString ClavisPaths::currentRelease() const
{
    return QDir(m_installPrefix).filePath(QStringLiteral("current"));
}
QString ClavisPaths::configHome() const { return m_configHome; }
QString ClavisPaths::dataHome() const { return m_dataHome; }
QString ClavisPaths::stateHome() const { return m_stateHome; }
QString ClavisPaths::cacheHome() const { return m_cacheHome; }
QString ClavisPaths::runtimeHome() const { return m_runtimeHome; }
QString ClavisPaths::profileName() const { return m_profileName; }
QString ClavisPaths::profileConfigHome() const { return m_profileConfigHome; }
QString ClavisPaths::profileHome() const
{
    return m_profileHome;
}
QString ClavisPaths::generatedHome() const
{
    return m_generatedHome;
}
QString ClavisPaths::qmlImportHome() const
{
    return m_qmlImportHome;
}
QString ClavisPaths::stableKey() const
{
    return m_stableKey;
}

QProcessEnvironment ClavisPaths::processEnvironment(const QString &releaseRoot) const
{
    const QString normalizedRelease = cleanAbsolute(releaseRoot);
    const QString qmlImport = QDir(normalizedRelease).filePath(QStringLiteral("lib/qml"));
    QProcessEnvironment result = QProcessEnvironment::systemEnvironment();
    result.insert(QStringLiteral("CLAVIS_BIN_HOME"), m_binHome);
    result.insert(QStringLiteral("CLAVIS_INSTALL_PREFIX"), m_installPrefix);
    result.insert(QStringLiteral("CLAVIS_RELEASE_ROOT"), normalizedRelease);
    result.insert(QStringLiteral("CLAVIS_CONFIG_HOME"), m_configHome);
    result.insert(QStringLiteral("CLAVIS_DATA_HOME"), m_dataHome);
    result.insert(QStringLiteral("CLAVIS_STATE_HOME"), m_stateHome);
    result.insert(QStringLiteral("CLAVIS_CACHE_HOME"), m_cacheHome);
    result.insert(QStringLiteral("CLAVIS_RUNTIME_HOME"), m_runtimeHome);
    result.insert(QStringLiteral("CLAVIS_PROFILE"), m_profileName);
    result.insert(QStringLiteral("CLAVIS_PROFILE_CONFIG_HOME"), profileConfigHome());
    result.insert(QStringLiteral("CLAVIS_PROFILE_HOME"), profileHome());
    result.insert(QStringLiteral("CLAVIS_GENERATED_HOME"), generatedHome());
    result.insert(QStringLiteral("CLAVIS_QML_IMPORT_HOME"), qmlImport);
    result.insert(QStringLiteral("CLAVIS_KEY"), stableKey());
    result.insert(
        QStringLiteral("PATH"),
        prependPath(QFileInfo(stableKey()).absolutePath(),
                    prependPath(m_binHome, result.value(QStringLiteral("PATH")))));
    result.insert(
        QStringLiteral("QML_IMPORT_PATH"),
        prependPath(qmlImport, result.value(QStringLiteral("QML_IMPORT_PATH"))));
    result.insert(
        QStringLiteral("QML2_IMPORT_PATH"),
        prependPath(qmlImport, result.value(QStringLiteral("QML2_IMPORT_PATH"))));
    return result;
}

QString ClavisPaths::cleanAbsolute(const QString &value)
{
    if (value.isEmpty() || !QDir::isAbsolutePath(value))
        return {};
    return QDir::cleanPath(value);
}

QString ClavisPaths::environmentPath(const char *overrideName,
                                     const char *xdgName,
                                     const QString &fallback,
                                     const QString &suffix)
{
    const QString overridePath = cleanAbsolute(env(overrideName));
    if (!overridePath.isEmpty())
        return overridePath;
    QString base = cleanAbsolute(env(xdgName));
    if (base.isEmpty())
        base = cleanAbsolute(fallback);
    return QDir(base).filePath(suffix);
}

} // namespace Clavis::Runtime
