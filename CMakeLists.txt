# TradeForge compiled core.
#
# The C++ core is OPTIONAL. The Python package, its test suite and the research
# pipeline all run without it; this target exists so the production path can be
# faster and so the reference implementation has something independent to be
# compared against.
#
#   cmake -S . -B build -DCMAKE_BUILD_TYPE=Release
#   cmake --build build -j
#   ctest --test-dir build --output-on-failure
#
# Sanitiser build (CI runs this separately):
#   cmake -S . -B build-asan -DCMAKE_BUILD_TYPE=Debug -DTRADEFORGE_SANITIZE=ON

cmake_minimum_required(VERSION 3.20)

project(
    TradeForge
    VERSION 0.1.0
    DESCRIPTION "Market microstructure, order book and execution research platform"
    LANGUAGES CXX
)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

option(TRADEFORGE_SANITIZE "Build with AddressSanitizer and UndefinedBehaviorSanitizer" OFF)
option(TRADEFORGE_BUILD_TESTS "Build the C++ unit tests" ON)
option(TRADEFORGE_BUILD_PYTHON "Build the pybind11 extension" ON)
# Downloading GoogleTest requires network access. Off by default so a build in a
# sandboxed or offline environment succeeds: use an installed GTest, or turn
# this on when you have a connection.
option(TRADEFORGE_FETCH_GTEST "Download GoogleTest if it is not installed" OFF)

# Warnings are errors in CI. A warning that is tolerated becomes a warning that
# is ignored, and then a bug that nobody saw.
add_library(tradeforge_warnings INTERFACE)
target_compile_options(
    tradeforge_warnings
    INTERFACE
        $<$<CXX_COMPILER_ID:AppleClang,Clang,GNU>:-Wall;-Wextra;-Wpedantic;-Wshadow;-Wconversion>
        $<$<CXX_COMPILER_ID:MSVC>:/W4>
)

if(TRADEFORGE_SANITIZE)
    add_library(tradeforge_sanitize INTERFACE)
    target_compile_options(
        tradeforge_sanitize INTERFACE -fsanitize=address,undefined -fno-omit-frame-pointer -g
    )
    target_link_options(tradeforge_sanitize INTERFACE -fsanitize=address,undefined)
else()
    add_library(tradeforge_sanitize INTERFACE)
endif()

# ---------------------------------------------------------------- core library

add_library(tradeforge_core_lib STATIC
    cpp/src/book.cpp
    cpp/src/matching.cpp
)
target_include_directories(tradeforge_core_lib PUBLIC cpp/include)
target_link_libraries(tradeforge_core_lib PRIVATE tradeforge_warnings tradeforge_sanitize)
set_target_properties(tradeforge_core_lib PROPERTIES POSITION_INDEPENDENT_CODE ON)

# --------------------------------------------------------------------- tests

if(TRADEFORGE_BUILD_TESTS)
    find_package(GTest QUIET)

    if(NOT GTest_FOUND AND TRADEFORGE_FETCH_GTEST)
        include(FetchContent)
        FetchContent_Declare(
            googletest
            GIT_REPOSITORY https://github.com/google/googletest.git
            GIT_TAG v1.15.2
            GIT_SHALLOW TRUE
        )
        set(gtest_force_shared_crt ON CACHE BOOL "" FORCE)
        FetchContent_MakeAvailable(googletest)
        set(GTest_FOUND TRUE)
    endif()

    if(GTest_FOUND)
        enable_testing()
        add_executable(tradeforge_cpp_tests cpp/tests/test_book.cpp)
        target_link_libraries(
            tradeforge_cpp_tests
            PRIVATE tradeforge_core_lib GTest::gtest_main tradeforge_warnings
                    tradeforge_sanitize
        )
        include(GoogleTest)
        gtest_discover_tests(tradeforge_cpp_tests)
        message(STATUS "TradeForge: C++ unit tests enabled")
    else()
        message(
            STATUS
                "TradeForge: GoogleTest not found, so the C++ unit tests are skipped. "
                "Install it (brew install googletest) or configure with "
                "-DTRADEFORGE_FETCH_GTEST=ON to download it. The Python test suite "
                "and the differential tests still run."
        )
    endif()
endif()

# ----------------------------------------------------------- python extension

if(TRADEFORGE_BUILD_PYTHON)
    find_package(Python3 COMPONENTS Interpreter Development.Module QUIET)
    if(Python3_FOUND)
        # pybind11 is installed in the project's Python environment; ask Python
        # for its CMake directory rather than assuming a system location.
        execute_process(
            COMMAND ${Python3_EXECUTABLE} -m pybind11 --cmakedir
            OUTPUT_VARIABLE PYBIND11_CMAKE_DIR
            OUTPUT_STRIP_TRAILING_WHITESPACE
            RESULT_VARIABLE PYBIND11_LOOKUP_RESULT
        )
        if(PYBIND11_LOOKUP_RESULT EQUAL 0)
            list(APPEND CMAKE_PREFIX_PATH "${PYBIND11_CMAKE_DIR}")
            find_package(pybind11 CONFIG QUIET)
        endif()

        if(pybind11_FOUND)
            pybind11_add_module(tradeforge_core cpp/src/bindings.cpp)
            target_link_libraries(tradeforge_core PRIVATE tradeforge_core_lib)
            target_compile_options(tradeforge_core PRIVATE $<$<CXX_COMPILER_ID:AppleClang,Clang,GNU>:-Wall;-Wextra>)
            set_target_properties(
                tradeforge_core
                PROPERTIES LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/python"
            )
            message(STATUS "TradeForge: pybind11 extension will be built")
        else()
            message(STATUS "TradeForge: pybind11 not found; skipping the Python extension")
        endif()
    else()
        message(STATUS "TradeForge: no Python development headers; skipping the extension")
    endif()
endif()
