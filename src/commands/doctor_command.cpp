#include "doctor_command.h"

#include "recording/dependency_probe.h"

#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QProcess>

using namespace Clavis::Recording;

namespace {

CommandResult cpuPowerDoctor(const QStringList &arguments)
{
    const bool jsonRequested = arguments.contains(QStringLiteral("--json"));
    for (const QString &argument : arguments) {
        if (argument != QStringLiteral("--json")) {
            return {
                2,
                jsonRequested,
                {},
                QStringLiteral("Unknown key doctor cpu-power option: %1")
                    .arg(argument),
                true,
            };
        }
    }

    const QString keytop = qEnvironmentVariable("CLAVIS_KEYTOP", "keytop");
    QProcess keytopProbe;
    keytopProbe.start(keytop, {
        QStringLiteral("value"),
        QStringLiteral("cpu"),
        QStringLiteral("--format"),
        QStringLiteral("json"),
    });
    const bool keytopStarted = keytopProbe.waitForStarted(2000);
    if (keytopStarted)
        keytopProbe.waitForFinished(5000);
    QJsonObject keytopCpu;
    if (keytopStarted && keytopProbe.exitStatus() == QProcess::NormalExit) {
        const QJsonDocument document = QJsonDocument::fromJson(
            keytopProbe.readAllStandardOutput());
        keytopCpu = document.object().value(QStringLiteral("cpu")).toObject();
    }
    const bool keytopPowerReadable = keytopCpu.value(
        QStringLiteral("powerWatts")).isDouble();
    const bool available = keytopStarted && !keytopCpu.isEmpty();
    const QString status = !keytopStarted
        ? QStringLiteral("keytop_not_installed")
        : keytopPowerReadable ? QStringLiteral("keytop_readable")
                              : QStringLiteral("keytop_available")
        ;
    const QString message = available
        ? QStringLiteral("CPU diagnostics are provided by the independent keytop process.")
        : QStringLiteral("Install keytop to inspect CPU power without duplicating its sampler.");
    const QJsonObject object{
        {QStringLiteral("schemaVersion"), 1},
        {QStringLiteral("command"), QStringLiteral("doctor.cpu-power")},
        {QStringLiteral("ok"), available},
        {QStringLiteral("status"), status},
        {QStringLiteral("keytopAvailable"), keytopStarted},
        {QStringLiteral("keytopPowerReadable"), keytopPowerReadable},
        {QStringLiteral("message"), message},
    };
    const QString text = QStringLiteral(
        "Clavis CPU power diagnostics:\n"
        "  status: %1\n"
        "  keytop: %2\n"
        "  %3")
        .arg(status, keytopStarted ? QStringLiteral("available") : QStringLiteral("not installed"), message);
    return {available ? 0 : 1, jsonRequested, object, text, !available};
}

} // namespace

CommandResult DoctorCommand::run(const QStringList &arguments) const
{
    if (!arguments.isEmpty()
        && arguments.first() == QStringLiteral("cpu-power")) {
        return cpuPowerDoctor(arguments.mid(1));
    }
    const bool jsonRequested = arguments.contains(QStringLiteral("--json"));
    QString outputDirectory;
    for (int index = 0; index < arguments.size(); ++index) {
        const QString argument = arguments.at(index);
        if (argument == QStringLiteral("--json"))
            continue;
        if (argument == QStringLiteral("--output") && index + 1 < arguments.size()) {
            outputDirectory = arguments.at(++index);
            continue;
        }
        const RecordingError error =
            makeError(QStringLiteral("usage_error"),
                      QStringLiteral("Unknown or incomplete doctor option: %1").arg(argument));
        return {
            UsageError,
            jsonRequested,
            {{QStringLiteral("schemaVersion"), SchemaVersion},
             {QStringLiteral("command"), QStringLiteral("doctor")},
             {QStringLiteral("ok"), false},
             {QStringLiteral("error"), error.toJson()}},
            error.message,
            true,
        };
    }
    if (outputDirectory.startsWith(QStringLiteral("~/")))
        outputDirectory.replace(0, 1, QDir::homePath());

    const QList<DependencyCheck> checks =
        DependencyProbe().run(outputDirectory, true);
    const bool ok = DependencyProbe::allPassed(checks);
    QJsonArray checksJson;
    QStringList lines;
    lines << QStringLiteral("Clavis Shell diagnostics:");
    for (const DependencyCheck &check : checks) {
        checksJson.append(check.toJson());
        lines << QStringLiteral("  [%1] %2: %3%4")
                     .arg(check.ok ? QStringLiteral("OK") : QStringLiteral("FAIL"),
                          check.name,
                          check.message,
                          check.path.isEmpty() ? QString()
                                               : QStringLiteral(" (%1)").arg(check.path));
    }

    const RecordingError error =
        ok ? RecordingError{}
           : makeError(QStringLiteral("doctor_failed"),
                       QStringLiteral("One or more required checks failed"));
    return {
        ok ? Success : DependencyFailure,
        jsonRequested,
        {{QStringLiteral("schemaVersion"), SchemaVersion},
         {QStringLiteral("command"), QStringLiteral("doctor")},
         {QStringLiteral("ok"), ok},
         {QStringLiteral("checks"), checksJson},
         {QStringLiteral("error"),
          error.isNull() ? QJsonValue(QJsonValue::Null) : QJsonValue(error.toJson())}},
        lines.join(QLatin1Char('\n')),
        !ok,
    };
}
