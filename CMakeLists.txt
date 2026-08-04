cmake_minimum_required(VERSION 3.21)
project(KeyCli VERSION 0.1.0 LANGUAGES CXX)

include(GNUInstallDirs)
include(CTest)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_AUTOMOC ON)

find_package(Qt6 REQUIRED COMPONENTS Core Gui Network)

add_subdirectory(src)

if(BUILD_TESTING)
    find_package(Qt6 REQUIRED COMPONENTS Test)
    add_subdirectory(tests)
endif()

install(DIRECTORY
    "${CMAKE_CURRENT_SOURCE_DIR}/packaging"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/key-cli"
    FILES_MATCHING PATTERN "*.py" PATTERN "*.json" PATTERN "*.in"
)
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/packaging/completions/key.bash"
    "${CMAKE_CURRENT_SOURCE_DIR}/packaging/completions/_key"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/key-cli/completions"
)
install(FILES
    "${CMAKE_CURRENT_SOURCE_DIR}/README.md"
    DESTINATION "${CMAKE_INSTALL_DATADIR}/key-cli"
)
