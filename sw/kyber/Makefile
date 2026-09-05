# ML-KEM-768 KAT builds with interchangeable NTT backends (plan step C6).
#
#   make TARGET=host BACKEND=SW           native, reference C NTT
#   make TARGET=host BACKEND=TIER1        native, Xkntt semantics emulated in C
#   make TARGET=rv32 BACKEND=SW           bare-metal RV32I for Spike
#   make TARGET=rv32 BACKEND=TIER1        ditto, using the real instructions
#   make TARGET=rv32 MARCH=rv32im         ... with the M extension (A14 onwards)
#
# MARCH defaults to rv32i and MUST keep doing so: it is the baseline every
# earlier measurement in docs/ was taken against, and A16 (MODS_A) exists
# precisely to be able to show both numbers rather than to replace one with the
# other.  The build path carries it for the same reason NTESTS does -- otherwise
# make hands back a stale binary built for the other ISA.
#
# NTESTS=n sets the iteration count; DIGEST=1 prints a SHAKE256 digest of the
# output instead of the output itself, which is what makes a full-length run on
# an instruction-set simulator affordable.

ROOT    := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
REF     := $(ROOT)/toolchain/kyber/ref
INC     := -I$(REF) -I$(ROOT)/sw/include -I$(dir $(lastword $(MAKEFILE_LIST)))

TARGET  ?= host
BACKEND ?= SW
NTESTS  ?= 10
DIGEST  ?= 0
MARCH   ?= rv32i

# NTESTS and DIGEST change the binary, so they belong in the path;
# otherwise make would hand back a stale build with the wrong count.
OUT     := build/$(TARGET)-$(BACKEND)-$(MARCH)-n$(NTESTS)-d$(DIGEST)
GEN     := build/ntt_tier1.c

REFSRC  := $(REF)/kem.c $(REF)/indcpa.c $(REF)/polyvec.c $(REF)/poly.c \
           $(REF)/cbd.c $(REF)/reduce.c $(REF)/verify.c $(REF)/fips202.c \
           $(REF)/symmetric-shake.c

ifeq ($(BACKEND),SW)
  NTTSRC := $(REF)/ntt.c
else
  NTTSRC := $(GEN)
endif

DEFS := -DKYBER_K=3 -DNTESTS=$(NTESTS)
ifeq ($(DIGEST),1)
  DEFS += -DKAT_DIGEST
endif

ifeq ($(TARGET),host)
  CC      := cc
  # The host has no custom instructions, so TIER1 uses the C emulation in
  # sw/include/xkntt.h -- the one tb/unit/test_insn_bridge.py holds to the
  # golden model.  That makes a 10000-iteration run take seconds.
  DEFS    += -DXKNTT_EMULATE=1
  CFLAGS  := -O2 -Wall -fomit-frame-pointer $(INC) $(DEFS)
  LDFLAGS :=
  SRC     := kat_main.c hostio.c
  BIN     := $(OUT)/kat
else
  CC      := riscv-none-elf-gcc
  # A29.  ENTROPY=1 swaps the deterministic KAT generator for one that reads
  # Zkr's `seed` CSR.  It is a SOURCE SELECTION and not a runtime flag: the KAT
  # build does not compile randombytes_seed.c at all, so it cannot reach CSR
  # 0x015 however it is invoked.  tb/unit/test_kat_no_seed.py checks that by
  # disassembling the binary rather than trusting this comment.
  ifeq ($(ENTROPY),1)
    ENTROPY_SRC  := randombytes_seed.c
    DEFS         += -DKAT_ENTROPY=1
    # Reading a CSR needs Zicsr in the -march string, and MARCH defaults to
    # plain rv32i on purpose -- line 9 says it MUST keep doing so, because it is
    # the baseline every other measurement is taken against.  So Zicsr is
    # appended for THIS build only, and only when it is actually needed.  The
    # assembler's error without it is `unrecognized opcode csrrw ... extension
    # zicsr required`, which is a build failure and not a silent omission.
    CFLAGS_EXTRA := _zicsr
  endif
  # CFLAGS uses `:=`, which expands NOW -- so the block above has to come
  # first or CFLAGS_EXTRA is empty and the Zicsr never reaches the assembler.
  # It did, on the first attempt, and the symptom was `unrecognized opcode
  # csrrw` with the flag apparently set.
  CFLAGS  := -march=$(MARCH)$(CFLAGS_EXTRA) -mabi=ilp32 -O2 -Wall \
             -fomit-frame-pointer -ffreestanding $(INC) $(DEFS)
  # -nostdlib for a bare-metal image.  -lgcc stays whatever MARCH is: rv32i has
  # no multiply instruction at all, and even rv32im has no 64-bit helpers.
  LDFLAGS := -nostdlib -nostartfiles -T link.ld -lgcc
  SRC     := kat_main.c htif.c crt0.S $(ENTROPY_SRC)
  BIN     := $(OUT)/kat.elf
endif

.PHONY: all clean gen
all: $(BIN)

gen: $(GEN)
$(GEN): gen_ntt_tier1.py $(ROOT)/model/ntt_ref.py $(REF)/ntt.c
	python3 gen_ntt_tier1.py $@

$(BIN): $(SRC) $(REFSRC) $(NTTSRC) | $(OUT)
	$(CC) $(CFLAGS) -o $@ $(SRC) $(REFSRC) $(NTTSRC) $(LDFLAGS)

ifneq ($(BACKEND),SW)
$(BIN): $(GEN)
endif

$(OUT):
	mkdir -p $(OUT)

clean:
	rm -rf build
