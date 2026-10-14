"""Compile and exercise the fixed transport with CPU stubs; not an NPU acceptance test."""
from pathlib import Path
import subprocess
import numpy as np
import pytest
import exec_case
import op_abi
import run_harness

HEADER = r"""
#pragma once
using aclrtStream=void*; using aclsolverHandle_t=void*;
inline int aclInit(const char*){return 0;}
inline int aclFinalize(){return 0;}
inline int aclrtSetDevice(int){return 0;}
inline int aclrtResetDevice(int){return 0;}
inline int aclrtCreateStream(aclrtStream *p){*p=(void*)1;return 0;}
inline int aclrtDestroyStream(aclrtStream){return 0;}
inline int aclrtSynchronizeStream(aclrtStream){return 0;}
inline int aclsolverCreate(aclsolverHandle_t *p){*p=(void*)1;return 0;}
inline int aclsolverDestroy(aclsolverHandle_t){return 0;}
inline int aclsolverSetStream(aclsolverHandle_t,aclrtStream){return 0;}
"""
ADAPTER = r"""
#include "solver_adapter.h"
#include <cstring>
#include <cstdlib>
#include <vector>
static std::vector<unsigned char> input;
static int iteration=0;
static void target(){}
extern "C" const SolverAdapterDesc *solver_adapter_descriptor(){
 static SolverAdapterDesc d={VERSION,sizeof(SolverAdapterDesc),OP,"float32","device","column_major","scalar",(const void*)&target};return &d;
}
extern "C" int solver_adapter_execute(void*,int *ret){++iteration;*ret=0;return 0;}
extern "C" int solver_adapter_destroy(void*){return 0;}
#if VERSION == 2
extern "C" int solver_adapter_create_v2(const SolverAdapterCaseV2 *c,void*,void*,void **state){
 if(c->base.struct_size!=sizeof(*c) || c->m!=3 || c->base.n!=2 || c->out_tau_count!=2) return 99;
 *state=(void*)1;return 0;
}
extern "C" int solver_adapter_reset_v2(void*,const void *a,size_t n,const void*,size_t,const int32_t*,size_t,const void*,size_t){input.assign((const unsigned char*)a,(const unsigned char*)a+n);return 0;}
extern "C" int solver_adapter_readback_v2(void*,void *out,size_t n,int32_t *info,size_t,int32_t*,size_t,void *tau,size_t t){
 if(n!=input.size() || t!=8) return 98;
 std::memcpy(out,input.data(),n);*info=0;
 float values[]={1.f, std::getenv("CHANGE_TAU")?float(iteration):2.f};std::memcpy(tau,values,t);return 0;
}
#else
extern "C" int solver_adapter_create(const SolverAdapterCase *c,void*,void*,void **state){if(c->struct_size!=sizeof(*c))return 99;*state=(void*)1;return 0;}
extern "C" int solver_adapter_reset(void*,const void *a,size_t n,const void*,size_t){input.assign((const unsigned char*)a,(const unsigned char*)a+n);return 0;}
extern "C" int solver_adapter_readback(void*,void *out,size_t n,int32_t *info,size_t){std::memcpy(out,input.data(),n);*info=0;return 0;}
#endif
"""

@pytest.mark.parametrize("version",[1,2])
def test_compiled_adapter_lifecycle_and_auxiliary_determinism(tmp_path,monkeypatch,version):
    (tmp_path/"cann_ops_solver.h").write_text(HEADER)
    op="sgeqrf" if version==2 else "spotrf"
    source=tmp_path/"adapter.cpp";source.write_text(ADAPTER)
    binary=tmp_path/"executor"
    subprocess.run(["g++","-std=c++17","-O1","-Wall",f"-DVERSION={version}",f'-DOP="{op}"',
                    "-I"+str(tmp_path),"-I"+str(exec_case.HERE.parents[1]/"assets/adapter"),
                    str(exec_case.HERE/"adapter_exec.cpp"),str(source),"-ldl","-o",str(binary)],
                   check=True,capture_output=True,text=True)
    abi=op_abi.get_adapter(op)
    case=dict(op=op,n=2,m=3 if version==2 else 2)
    spec=run_harness.build_spec(abi,case,3,0,"column_major")
    inputs={"in_a":np.arange(case["m"]*2,dtype=np.float32).reshape(case["m"],2)}
    rec=exec_case.run_case(binary,tmp_path/"run",spec,inputs,tmp_path,tmp_path)
    assert rec["kind"]=="ok",rec
    assert exec_case.rerun_record(rec["result"])["consistent"] is True
    np.testing.assert_array_equal(rec["out32"],inputs["in_a"])
    if version==2:
        np.testing.assert_array_equal(rec["extras"]["tau"],[1,2])
        monkeypatch.setenv("CHANGE_TAU","1")
        rec=exec_case.run_case(binary,tmp_path/"run",spec,inputs,tmp_path,tmp_path)
        assert rec["kind"]=="rerun_mismatch"
        assert rec["result"]["rerun_first_diff_key"]=="tau"
        assert (tmp_path/"run/out/tau.bin.diff.bin").is_file()
