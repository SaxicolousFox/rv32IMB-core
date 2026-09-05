/*
 * Dhrystone's platform hook, and the check that the benchmark actually ran.
 *
 * setStats() is called by dhrystone_main.c immediately outside its own
 * Start_Timer/Stop_Timer pair, so it is the natural place to sample minstret
 * alongside mcycle: IPC needs both counters over windows that are the same to
 * within a few instructions, and this is the only pair of points where that is
 * true without editing the benchmark.  Both windows are reported, so the fact
 * that setStats's is a handful of instructions WIDER is visible in the output
 * rather than assumed away; tb/fpga/parse_bench_uart.py checks they agree.
 *
 * The snapshot is here for a less obvious reason.  Dhrystone's Ptr_Glob and
 * Next_Ptr_Glob come from alloca() INSIDE main(), so the moment dhry_main()
 * returns they point at dead stack -- and on a bare-metal machine with nothing
 * to reuse it, reading them afterwards would usually still "work", which is the
 * worst possible behaviour for a check.  setStats(0) fires while that frame is
 * still live and the loop has just finished, i.e. exactly when the published
 * "final values of the variables" are final.  Copying the two records out there
 * makes the verification well-defined instead of luckily-correct.
 */
#include "dhrystone.h"
#include "util.h"
#include "bench_io.h"

unsigned int bench_stat_cyc0, bench_stat_cyc1;
unsigned int bench_stat_ins0, bench_stat_ins1;

extern int         Int_Glob;
extern Boolean     Bool_Glob;
extern char        Ch_1_Glob, Ch_2_Glob;
extern int         Arr_1_Glob[50];
extern int         Arr_2_Glob[50][50];
extern Rec_Pointer Ptr_Glob, Next_Ptr_Glob;

static Rec_Type snap_ptr, snap_next;
static int      snap_taken;

void setStats(int enable)
{
    if (enable) {
        /* minstret first on entry and last on exit, so the instruction window
         * strictly contains the cycle window rather than straddling it. */
        /* A20: the HPM snapshot goes OUTSIDE the cycle window on entry and
         * outside it on exit, for the same reason minstret does -- six csrr's
         * are six cycles, and they must not land inside the region they are
         * describing. */
#ifdef BENCH_HPM
        bench_hpm_read(bench_hpm_dhry0);
#endif
        bench_stat_ins0 = bench_minstret();
        bench_stat_cyc0 = bench_mcycle();
    } else {
        bench_stat_cyc1 = bench_mcycle();
        bench_stat_ins1 = bench_minstret();
#ifdef BENCH_HPM
        bench_hpm_read(bench_hpm_dhry1);
#endif
        snap_ptr   = *Ptr_Glob;
        snap_next  = *Next_Ptr_Glob;
        snap_taken = 1;
    }
}

static const char SOME_STRING[] = "DHRYSTONE PROGRAM, SOME STRING";

/* One bit per published expectation, so a failure says WHICH one.  A single
 * OK/BAD would make a wrong Arr_2_Glob indistinguishable from a wrong CRC in a
 * benchmark that has no other output. */
unsigned int dhry_verify(int runs)
{
    unsigned int bad = 0;

    if (!snap_taken)                            bad |= 1u << 0;
    if (Int_Glob != 5)                          bad |= 1u << 1;
    if (Bool_Glob != 1)                         bad |= 1u << 2;
    if (Ch_1_Glob != 'A')                       bad |= 1u << 3;
    if (Ch_2_Glob != 'B')                       bad |= 1u << 4;
    if (Arr_1_Glob[8] != 7)                     bad |= 1u << 5;
    if (Arr_2_Glob[8][7] != runs + 10)          bad |= 1u << 6;
    if (snap_ptr.Discr != Ident_1)              bad |= 1u << 7;
    if (snap_ptr.variant.var_1.Enum_Comp != Ident_3)  bad |= 1u << 8;
    if (snap_ptr.variant.var_1.Int_Comp != 17)  bad |= 1u << 9;
    if (strcmp(snap_ptr.variant.var_1.Str_Comp, SOME_STRING) != 0)
                                                bad |= 1u << 10;
    if (snap_next.Discr != Ident_1)             bad |= 1u << 11;
    if (snap_next.variant.var_1.Enum_Comp != Ident_2) bad |= 1u << 12;
    if (snap_next.variant.var_1.Int_Comp != 18) bad |= 1u << 13;
    if (strcmp(snap_next.variant.var_1.Str_Comp, SOME_STRING) != 0)
                                                bad |= 1u << 14;
    return bad;
}
