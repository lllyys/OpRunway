// Fixed executor for developer adapters; no developer main or verdict code.
#include <dlfcn.h>
#include <algorithm>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>
#include "cann_ops_solver.h"
#include "solver_adapter.h"
namespace {
#include "executor_io.hpp"
}
int main(int argc, char **argv) {
    if (argc != 2) return kSpecError;
    Spec s; std::string error;
    if (!s.Load(argv[1], &error)) { std::fprintf(stderr, "%s\n", error.c_str()); return kSpecError; }
    Result r; const std::string result=s.Str("result");
    const int runs=static_cast<int>(s.Num("runs"));
    r.SetNum("runs",runs); r.SetNum("rerun_runs_requested",runs);
    r.SetNum("rerun_runs_completed",0); r.Set("rerun_consistent","unknown");
    const SolverAdapterDesc *d=solver_adapter_descriptor();
    if (!d || (d->version!=SOLVER_ADAPTER_VERSION && d->version!=SOLVER_ADAPTER_VERSION_V2) || d->struct_size!=sizeof(*d)
        || !d->op || !d->dtype || !d->dut_abi || !d->dut_layout || !d->info_kind || !d->target_symbol
        || s.Str("op")!=d->op || s.Str("dtype")!=d->dtype || s.Str("abi")!=d->dut_abi
        || s.Str("layout")!=d->dut_layout || s.Str("info_kind")!=d->info_kind)
        return Bail(r,result,kSpecError,"spec_error","adapter descriptor/version differs from requested contract");
    const bool v2=d->version==SOLVER_ADAPTER_VERSION_V2;
    if ((s.Num("adapter_version",1)==2 && !v2)
        || (v2 && (!solver_adapter_create_v2 || !solver_adapter_reset_v2 || !solver_adapter_readback_v2))
        || (!v2 && (!solver_adapter_create || !solver_adapter_reset || !solver_adapter_readback)))
        return Bail(r,result,kSpecError,"spec_error","required adapter protocol/callbacks unavailable");
    r.SetNum("adapter_version",d->version);
    r.Set("transport_layout","host_compact_row_major");
    r.Set("dut_layout",d->dut_layout); r.Set("dut_abi",d->dut_abi);
    Dl_info lib{};
    if (!dladdr(const_cast<void*>(d->target_symbol),&lib) || !lib.dli_fname)
        return Bail(r,result,kSpecError,"spec_error","cannot identify target operator library");
    r.Set("lib_path",lib.dli_fname);
    SolverAdapterCaseV2 c2{}; SolverAdapterCase &c=c2.base;
    c.version=d->version; c.struct_size=v2?sizeof(c2):sizeof(c);
    c.n=s.Num("n"); c.nrhs=s.Num("nrhs"); c.batch=s.Num("batch",1);
    c.lda=s.Num("lda",c.n); c.ldb=s.Num("ldb",c.n);
    const std::string uplo=s.Str("uplo","L"),probe=s.Str("info_probe"),kind=s.Str("input_kind","matrix");
    c.uplo=uplo.empty()?'?':uplo[0]; c.info_probe=probe.c_str(); c.input_kind=kind.c_str();
    const size_t width=s.Str("dtype")=="complex64"?8:s.Str("dtype")=="float32"?4:0;
    if (!width || c.n<=0 || c.batch<=0 || c.nrhs<0 || runs<=0)
        return Bail(r,result,kSpecError,"spec_error","v1 matrix transport requires positive n/batch/runs");
    size_t rows=0, cells=0;
    const int64_t ar=s.Num("a_rows",c.n), ac=s.Num("a_cols",c.n);
    const int64_t br=s.Num("b_rows",c.n), bc=s.Num("b_cols",c.nrhs);
    const int64_t outr=s.Num("out_rows",c.n), outc=s.Num("out_cols",c.n);
    if (ar<=0 || ac<0 || br<0 || bc<0 || outr<=0 || outc<=0
        || !MulSize(c.batch,ar,&rows) || !MulSize(rows,ac,&cells) || !MulSize(cells,width,&c.a_bytes)
        || !MulSize(c.batch,br,&rows) || !MulSize(rows,bc,&cells) || !MulSize(cells,width,&c.b_bytes))
        return Bail(r,result,kSpecError,"spec_error","invalid dimensions or buffer size overflow");
    if (v2) {
        if (!MulSize(c.batch,outr,&rows) || !MulSize(rows,outc,&cells) || !MulSize(cells,width,&c.out_bytes))
            return Bail(r,result,kSpecError,"spec_error","output size overflow");
    } else c.out_bytes=s.Str("call_shape")=="potrs_b_inplace"?c.b_bytes:c.a_bytes;
    c2.m=s.Num("m",c.n);c2.k=s.Num("k",c.n);c2.ldc=s.Num("ldc",c2.m);c2.ldx=s.Num("ldx",c.n);
    const std::string side=s.Str("side","LEFT"),trans=s.Str("trans","N");
    c2.side=side.empty()?'?':side[0];c2.trans=trans.empty()?'?':trans[0];
    auto count=[&](const char *key,size_t *value) {
        const auto n=s.Num(key,0);return n>=0 && MulSize(c.batch,n,value);
    };
    if (!count("in_ipiv_count",&c2.in_ipiv_count) || !count("in_tau_count",&c2.in_tau_count)
        || !count("out_ipiv_count",&c2.out_ipiv_count) || !count("out_tau_count",&c2.out_tau_count))
        return Bail(r,result,kSpecError,"spec_error","auxiliary count overflow");
    size_t itb=0,otb=0,ipb=0,opb=0;
    if (!MulSize(c2.in_tau_count,width,&itb) || !MulSize(c2.out_tau_count,width,&otb)
        || !MulSize(c2.in_ipiv_count,sizeof(int32_t),&ipb) || !MulSize(c2.out_ipiv_count,sizeof(int32_t),&opb))
        return Bail(r,result,kSpecError,"spec_error","auxiliary size overflow");
    std::vector<int32_t> in_ipiv(c2.in_ipiv_count),out_ipiv(c2.out_ipiv_count),first_ipiv;
    std::vector<unsigned char> in_tau(itb),out_tau(otb),first_tau;
    if ((ipb && !ReadAll(s.Str("in_ipiv"),in_ipiv.data(),ipb))
        || (itb && !ReadAll(s.Str("in_tau"),in_tau.data(),itb)))
        return Bail(r,result,kIoError,"io_error","missing or incorrectly sized ipiv/tau input");
    c.info_count=s.Str("info_kind")=="array"?static_cast<size_t>(c.batch):1;
    std::vector<unsigned char> a(c.a_bytes), b(c.b_bytes), out(c.out_bytes), first;
    std::vector<int32_t> info(c.info_count), first_info;
    if ((a.size()&&!ReadAll(s.Str("in_a"),a.data(),a.size())) || (b.size()&&!ReadAll(s.Str("in_b"),b.data(),b.size())))
        return Bail(r,result,kIoError,"io_error","missing or incorrectly sized input");
    r.Set("out32_dtype",d->dtype); r.SetNum("out32_bytes",c.out_bytes); r.SetNum("info_len",c.info_count);
    if (!r.Flush(result)) return kIoError;
    bool initialized=false,device_set=false; void *state=nullptr;
    aclrtStream stream=nullptr; aclsolverHandle_t handle=nullptr;
    int code=kOk,completed=0; bool mismatch=false;
    int device=static_cast<int>(s.Num("device_id"));
    auto fail=[&](int exit,const std::string &what) { code=exit;error=what; };
    do {
        if (aclInit(nullptr)) { fail(kAclError,"aclInit failed");break; } initialized=true;
        if (aclrtSetDevice(device)) { fail(kAclError,"aclrtSetDevice failed");break; } device_set=true;
        if (aclrtCreateStream(&stream) || aclsolverCreate(&handle) || aclsolverSetStream(handle,stream)) {
            fail(kAclError,"stream/handle setup failed");break;
        }
        int rc=v2?solver_adapter_create_v2(&c2,handle,stream,&state):solver_adapter_create(&c,handle,stream,&state);
        if (rc) { fail(kOpError,"adapter create ret="+std::to_string(rc));break; }
        for (int i=1;i<=runs;++i) {
            rc=v2?solver_adapter_reset_v2(state,a.data(),a.size(),b.data(),b.size(),
                in_ipiv.data(),in_ipiv.size(),in_tau.data(),in_tau.size()):
                solver_adapter_reset(state,a.data(),a.size(),b.empty()?nullptr:b.data(),b.size());
            if (rc || aclrtSynchronizeStream(stream)) { fail(kOpError,"adapter reset failed");break; }
            int dut=-1; rc=solver_adapter_execute(state,&dut);
            r.SetNum("run"+std::to_string(i)+"_ret",dut);
            if (rc) { fail(kOpError,"adapter execute failed");break; }
            if (aclrtSynchronizeStream(stream)) { fail(kAclError,"target synchronization failed");break; }
            rc=v2?solver_adapter_readback_v2(state,out.data(),out.size(),info.data(),info.size(),
                out_ipiv.data(),out_ipiv.size(),out_tau.data(),out_tau.size()):
                solver_adapter_readback(state,out.data(),out.size(),info.data(),info.size());
            if (rc) { fail(kOpError,"adapter readback failed");break; }
            const std::string suffix=i==1?"":dut?".fail.bin":".diff.bin";
            if (i==1 || dut || out!=first || info!=first_info || out_ipiv!=first_ipiv || out_tau!=first_tau) {
                if (!WriteAll(s.Str("out_a")+suffix,out.data(),out.size())
                    || !WriteAll(s.Str("out_info")+suffix,info.data(),info.size()*sizeof(int32_t))
                    || (opb && !WriteAll(s.Str("out_ipiv")+suffix,out_ipiv.data(),opb))
                    || (otb && !WriteAll(s.Str("out_tau")+suffix,out_tau.data(),otb))) {
                    fail(kIoError,"cannot preserve output");break;
                }
            }
            if (dut) { r.SetNum("fail_run",i);fail(kOpError,"operator ret="+std::to_string(dut));break; }
            ++completed;
            if (i==1) { first=out;first_info=info;first_ipiv=out_ipiv;first_tau=out_tau; }
            else if (out!=first || info!=first_info || out_ipiv!=first_ipiv || out_tau!=first_tau) {
                mismatch=true;r.SetNum("rerun_first_diff_run",i);
                r.Set("rerun_first_diff_key",out!=first?"out32":info!=first_info?"info":out_ipiv!=first_ipiv?"ipiv":"tau");fail(kRerunMismatch,"bitwise mismatch");break;
            }
            r.SetNum("rerun_runs_completed",completed);
            if (!r.Flush(result)) { fail(kIoError,"cannot checkpoint result");break; }
        }
    } while(false);
    Teardown td;
    if (stream) td.Note("sync before cleanup",aclrtSynchronizeStream(stream));
    td.Note("adapter destroy",solver_adapter_destroy(state));
    if (handle) td.Note("handle",aclsolverDestroy(handle));
    if (stream) td.Note("stream",aclrtDestroyStream(stream));
    if (device_set) td.Note("device",aclrtResetDevice(device));
    if (initialized) td.Note("acl",aclFinalize());
    r.SetNum("teardown_ret",td.first_ret());r.Set("teardown_detail",td.detail());
    if (td.first_ret() && code==kOk) fail(kAclError,td.detail());
    r.SetNum("rerun_runs_completed",completed);
    r.Set("rerun_consistent",mismatch?"0":completed==runs?"1":"unknown");
    r.SetNum("exit",code);r.Set("status",code?"error":"ok");r.Set("detail",error);
    return r.Flush(result)?code:kIoError;
}
