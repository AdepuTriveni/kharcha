# Native on-device parser (PROJECT_SPEC 15.6). Enabled with:
#   ./gradlew -Pkharcha.llama=true -Pllama.dir=/path/to/llama.cpp assembleSideloadDebug
cmake_minimum_required(VERSION 3.22.1)
project(kharcha_llama CXX C)

set(CMAKE_CXX_STANDARD 17)
set(LLAMA_BUILD_COMMON OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(GGML_OPENMP OFF CACHE BOOL "" FORCE)

add_subdirectory(${LLAMA_DIR} llama-build)

add_library(kharcha_llama SHARED kharcha_llama.cpp)
target_link_libraries(kharcha_llama PRIVATE llama android log)
