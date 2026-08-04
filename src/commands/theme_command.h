#pragma once

#include "command_result.h"

#include <QStringList>

class ThemeCommand {
public:
    CommandResult run(const QStringList &arguments) const;
};
