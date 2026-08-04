#include "management_command.h"

#include "clavis_release.h"
#include "runtime/clavis_paths.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QProcess>
#include <QProcessEnvironment>

using Clavis::Runtime::ClavisPaths;

namespace {

QString currentReleaseRoot()
{
    const QString configured = QString::fromLocal8Bit(
        qgetenv("CLAVIS_RELEASE_ROOT")).trimmed();
    if (!configured.isEmpty() && QDir::isAbsolutePath(configured))
        return QDir::cleanPath(configured);

    QDir executableDir(QCoreApplication::applicationDirPath());
    if (executableDir.dirName() == QStringLiteral("bin")) {
        executableDir.cdUp();
        if (QFileInfo::exists(executableDir.filePath(QStringLiteral("release.json"))))
            return executableDir.absolutePath();
    }
    return {};
}

QString managerPath(const QString &releaseRoot)
{
    const QString overridePath = QString::fromLocal8Bit(
        qgetenv("CLAVIS_MANAGER")).trimmed();
    if (!overridePath.isEmpty() && QDir::isAbsolutePath(overridePath))
        return QDir::cleanPath(overridePath);

    const QString libexecOverride = QString::fromLocal8Bit(
        qgetenv("CLAVIS_KEY_CLI_LIBEXEC")).trimmed();
    if (!libexecOverride.isEmpty() && QDir::isAbsolutePath(libexecOverride)) {
        const QString installed = QDir(libexecOverride).filePath(
            QStringLiteral("clavis-manager.py"));
        if (QFileInfo(installed).isFile())
            return installed;
    }

    QDir executableDir(QCoreApplication::applicationDirPath());
    if (executableDir.dirName() == QStringLiteral("bin")) {
        executableDir.cdUp();
        const QString installed = executableDir.filePath(
            QStringLiteral("libexec/key/clavis-manager.py"));
        if (QFileInfo(installed).isFile())
            return installed;
    }

    QDir source(QDir::currentPath());
    while (!source.isRoot()) {
        const QString candidate = source.filePath(
            QStringLiteral("packaging/clavis-manager.py"));
        if (QFileInfo(candidate).isFile())
            return candidate;
        source.cdUp();
    }
    if (!releaseRoot.isEmpty()) {
        const QString installed = QDir(releaseRoot).filePath(
            QStringLiteral("libexec/key/clavis-manager.py"));
        return installed;
    }
    return {};
}

} // namespace

CommandResult ManagementCommand::run(const QString &command,
                                     const QStringList &arguments) const
{
    const QString releaseRoot = currentReleaseRoot();
    const QString manager = managerPath(releaseRoot);
    if (manager.isEmpty() || !QFileInfo(manager).isFile()) {
        return {
            1,
            false,
            {},
            manager.isEmpty()
                ? QStringLiteral(
                    "key: release management requires an installed Clavis "
                    "release (or an absolute CLAVIS_MANAGER override)")
                : QStringLiteral("key: Clavis manager is missing: %1").arg(manager),
            true,
        };
    }

    QProcess process;
    process.setProgram(QStringLiteral("python3"));
    process.setArguments(QStringList{manager, command} + arguments);
    const ClavisPaths paths = ClavisPaths::fromEnvironment();
    QProcessEnvironment environment = releaseRoot.isEmpty()
        ? QProcessEnvironment::systemEnvironment()
        : paths.processEnvironment(releaseRoot);
    const QFileInfo currentExecutable(QCoreApplication::applicationFilePath());
    const QString currentKey = currentExecutable.canonicalFilePath().isEmpty()
        ? currentExecutable.absoluteFilePath()
        : currentExecutable.canonicalFilePath();
    environment.insert(QStringLiteral("CLAVIS_KEY"), currentKey);
    environment.insert(QStringLiteral("CLAVIS_KEY_CLI_LIBEXEC"),
                       QFileInfo(manager).absolutePath());
    process.setProcessEnvironment(environment);
    process.setProcessChannelMode(QProcess::ForwardedChannels);
    process.start();
    if (!process.waitForStarted()) {
        return {
            1,
            false,
            {},
            QStringLiteral("key: unable to start Clavis manager: %1")
                .arg(process.errorString()),
            true,
        };
    }
    process.waitForFinished(-1);
    if (process.exitStatus() != QProcess::NormalExit)
        return {128, false, {}, {}, true, true};
    return {process.exitCode(), false, {}, {}, process.exitCode() != 0, true};
}
