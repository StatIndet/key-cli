#include "weather_command.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QProcess>

namespace {

QString sourceProviderFrom(QDir directory)
{
    while (true) {
        const QString candidate = directory.filePath(
            QStringLiteral("packaging/weather.py"));
        if (QFileInfo(candidate).isFile())
            return candidate;
        if (!directory.cdUp())
            return {};
    }
}

QString providerPath()
{
    const QString overridePath = QString::fromLocal8Bit(
        qgetenv("CLAVIS_WEATHER_PROVIDER")).trimmed();
    if (!overridePath.isEmpty() && QFileInfo::exists(overridePath))
        return QDir::cleanPath(overridePath);

    QDir executableDir(QCoreApplication::applicationDirPath());
    if (executableDir.dirName() == QStringLiteral("bin")) {
        QDir prefixDir(executableDir);
        prefixDir.cdUp();
        const QString installed = prefixDir.filePath(
            QStringLiteral("share/key-cli/weather.py"));
        if (QFileInfo(installed).isFile())
            return installed;
    }

    const QString executableSource = sourceProviderFrom(executableDir);
    return executableSource.isEmpty()
        ? sourceProviderFrom(QDir(QDir::currentPath()))
        : executableSource;
}

} // namespace

CommandResult WeatherCommand::run(const QStringList &arguments) const
{
    const bool jsonRequested = arguments.contains(QStringLiteral("--json"));
    const QString provider = providerPath();
    if (provider.isEmpty()) {
        return {
            1,
            jsonRequested,
            {{QStringLiteral("ok"), false},
             {QStringLiteral("error"), QStringLiteral("weather_provider_missing")}},
            QStringLiteral("key: weather provider is not installed"),
            true,
        };
    }

    QStringList providerArguments = arguments;
    if (!jsonRequested)
        providerArguments.append(QStringLiteral("--text"));

    QProcess process;
    process.setProgram(QStringLiteral("python3"));
    process.setArguments(QStringList{provider} + providerArguments);
    process.setProcessChannelMode(QProcess::SeparateChannels);
    process.start();
    if (!process.waitForStarted()) {
        return {
            1,
            jsonRequested,
            {},
            QStringLiteral("key: unable to start weather provider: %1")
                .arg(process.errorString()),
            true,
        };
    }
    process.waitForFinished(30000);
    if (process.state() != QProcess::NotRunning) {
        process.kill();
        process.waitForFinished();
        return {1, jsonRequested, {},
                QStringLiteral("key: weather request timed out"), true};
    }

    const QByteArray stdoutBytes = process.readAllStandardOutput();
    const QString stderrText = QString::fromLocal8Bit(
        process.readAllStandardError()).trimmed();
    if (jsonRequested) {
        QJsonParseError parseError;
        const QJsonDocument document = QJsonDocument::fromJson(
            stdoutBytes.trimmed(), &parseError);
        if (!document.isObject() || parseError.error != QJsonParseError::NoError) {
            return {
                process.exitCode() == 0 ? 1 : process.exitCode(),
                true,
                {},
                stderrText.isEmpty()
                    ? QStringLiteral("key: weather provider returned invalid JSON")
                    : stderrText,
                true,
            };
        }
        return {
            process.exitCode(),
            true,
            document.object(),
            stderrText,
            process.exitCode() != 0,
        };
    }

    const QString text = QString::fromLocal8Bit(stdoutBytes).trimmed();
    return {
        process.exitCode(),
        false,
        {},
        text.isEmpty() ? stderrText : text,
        process.exitCode() != 0,
    };
}
