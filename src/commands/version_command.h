#pragma once

#include "command_result.h"

#include <QStringList>

class VersionCommand {
public:
    CommandResult run(const QStringList &arguments) const;
};
