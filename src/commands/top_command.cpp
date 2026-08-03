#include "top_command.h"

#include <QCoreApplication>
#include <QFileInfo>
#include <QProcess>

namespace {

QString keytopExecutable()
{
    const QString configured = QString::fromLocal8Bit(qgetenv("CLAVIS_KEYTOP"))
        .trimmed();
    if (!configured.isEmpty())
        return configured;
    return QStringLiteral("keytop");
}

} // namespace

CommandResult TopCommand::run(const QStringList &arguments) const
{
    QProcess process;
    process.setProgram(keytopExecutable());
    process.setArguments(QStringList{QStringLiteral("top")} + arguments);
    process.setProcessChannelMode(QProcess::ForwardedChannels);
    process.start();
    if (!process.waitForStarted()) {
        return {
            1,
            false,
            {},
            QStringLiteral("key: unable to start keytop: %1")
                .arg(process.errorString()),
            true,
        };
    }
    process.waitForFinished(-1);
    if (process.exitStatus() != QProcess::NormalExit)
        return {128, false, {}, {}, true, true};
    return {process.exitCode(), false, {}, {}, process.exitCode() != 0, true};
}

