cmake_minimum_required(VERSION 3.16)
project(sgetrf_sample LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT DEFINED ENV{ASCEND_HOME_PATH})
  message(FATAL_ERROR "Source CANN set_env.sh first")
endif()
set(ASCEND_CANN_PACKAGE_PATH "$ENV{ASCEND_HOME_PATH}")
set(SOC_VERSION "ascend910_93" CACHE STRING "Target SOC")
if(SOC_VERSION MATCHES "^ascend950")
  set(NPU_ARCH "dav-3510")
elseif(SOC_VERSION MATCHES "^ascend910(b|_93)")
  set(NPU_ARCH "dav-2201")
else()
  message(FATAL_ERROR "Unsupported SOC: ${SOC_VERSION}")
endif()
find_package(ASC REQUIRED)
enable_language(ASC)
set(SOURCES src/sgetrf/sgetrf_host.cpp src/sgetrf/sgetrf_kernel.cpp
            src/utils/aclsolver_auxiliary.cpp)
set_source_files_properties(${SOURCES} PROPERTIES LANGUAGE ASC)
add_library(ops_solver SHARED ${SOURCES})
target_include_directories(ops_solver BEFORE PRIVATE include
  "${ASCEND_CANN_PACKAGE_PATH}/pkg_inc/op_common"
  "${ASCEND_CANN_PACKAGE_PATH}/pkg_inc/base"
  "${ASCEND_CANN_PACKAGE_PATH}/pkg_inc")
target_compile_options(ops_solver PRIVATE
  $<$<COMPILE_LANGUAGE:ASC>:--npu-arch=${NPU_ARCH}>
  $<$<COMPILE_LANGUAGE:ASC>:-iquote${CMAKE_CURRENT_SOURCE_DIR}/include>)
target_link_libraries(ops_solver PRIVATE tiling_api platform)
add_library(sgetrf_adapter SHARED test/sgetrf/sgetrf_adapter.cpp)
target_include_directories(sgetrf_adapter PRIVATE include test/sgetrf
  "${ASCEND_CANN_PACKAGE_PATH}/include")
target_link_libraries(sgetrf_adapter PRIVATE ops_solver)
install(TARGETS ops_solver LIBRARY DESTINATION lib64)
install(FILES include/cann_ops_solver.h include/cann_ops_solver_common.h DESTINATION include)
