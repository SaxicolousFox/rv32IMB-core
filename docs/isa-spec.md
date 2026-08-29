# Xkntt — ML-KEM NTT vendor extension, version 0.1

**Status: FROZEN for Phase 1.** This document is the contract between four
artifacts that must agree bit-for-bit:

1. the **RTL decoder** (Track A / I1),
2. the **Spike model** (Track C1),
3. the **LLVM assembler, disassembler and intrinsics** (Track C2/C3),
4. the **test vectors**.

If any of the four disagrees with this document, the document wins — or the
document changes first and all four follow. Changing an encoding after Tracks A,
B and C have forked is expensive, which is the entire reason this exists.

ISA string: **`rv32i_zicsr_zicntr_xkntt0p1`**.

## Normative language

**MUST** / **MUST NOT** are architectural requirements: an implementation that
violates one is wrong. **UNDEFINED** means an implementation may do anything,
and software MUST NOT rely on the behaviour. **Reserved** fields MUST be zero;
see [Decode rules](#decode-rules).

## Machine-checked

Everything in the encoding summary table is verified on every `make regress` by
`tb/unit/test_isa_encoding.py`, which parses the hex literals **out of this
file** and checks them against the executable model (`model/isa/xkntt.py`) and
against stock GNU `as` via `.insn`. The document cannot silently drift from the
implementation.

The *semantics* are verified by `tb/unit/test_isa_semantics.py`, which builds
`ntt()`, `invntt()` and `basemul()` out of nothing but these instructions and
compares against the P0.3 golden model.

---

## 1. Frozen architectural decisions

These come from the development plan §1 and are restated here because the
encoding depends on them.

| Decision | Value | Source |
|---|---|---|
| Modulus | `q = 3329` | plan §1.3 |
| Montgomery radix | `R = 2^16` | plan §1.3 |
| `QINV` | `q^-1 mod 2^16` = `62209` (`-3327` signed) | plan §1.3 |
| Barrett constant | `v = ((1<<26) + q/2)/q = 20159` | plan §1.3 |
| Coefficient packing | two signed 16-bit per 32-bit register, `{hi, lo}` | plan §1.2 |
| Reduction strategy | Montgomery, **lazy** — outputs are NOT fully reduced | plan §1.3 |

### 1.1 The lazy-reduction rule

Results are congruent mod `q` but **not** normalised to `[0, q)`. The reference
NTT output is in a lazy range and this extension reproduces it exactly. An
implementation that normalises will disagree with the reference, and the
resulting failures look like arithmetic bugs but are not. Do not normalise.

### 1.2 Reference arithmetic primitives

All semantics below are expressed in terms of these two functions, which are
transcribed from `pq-crystals/kyber` `ref/reduce.c` and MUST be reproduced with
exactly these integer widths and truncations:

```c
int16_t montgomery_reduce(int32_t a) {
    int16_t t = (int16_t)a * QINV;      /* low 16 bits of a, times QINV */
    t = (a - (int32_t)t * KYBER_Q) >> 16;
    return t;                            /* in (-q, q), NOT fully reduced */
}

int16_t barrett_reduce(int16_t a) {
    int16_t t = ((int32_t)20159 * a + (1 << 25)) >> 26;
    t *= KYBER_Q;
    return a - t;                        /* in [-(q-1)/2, (q-1)/2] */
}
```

`fqmul(a, b)` is shorthand for `montgomery_reduce((int32_t)a * b)`.

The executable versions live in `model/modarith.py` and were validated
bit-exactly against the C reference over 1261 polynomials in P0.3.

### 1.3 Packing and sign extension

A packed register holds two coefficients:

```
 31                    16 15                     0
+------------------------+------------------------+
|          hi            |           lo           |
+------------------------+------------------------+
```

For a butterfly at index `j` with half-length `len`, `lo = f[j]` and
`hi = f[j+len]`. Both halves are **signed 16-bit**; `sext16()` below means
sign-extension of a 16-bit field to 32 bits.

Where an instruction reads only `rs[15:0]` (a scalar operand such as a zeta),
the upper half of that register is **ignored** — not reserved. Software need not
clear it. This is distinct from reserved *encoding* fields, which MUST be zero.

---

## 2. Opcode space

| Name | Encoding | Used for |
|---|---|---|
| `custom-0` | `0b0001011` = `0x0B` | **Tier 1** — tightly-coupled ALU-class |
| `custom-1` | `0b0101011` = `0x2B` | **Tier 2** — block coprocessor control |
| `custom-2` | `0b1011011` = `0x5B` | **avoided** — reserved for RV128 |
| `custom-3` | `0b1111011` = `0x7B` | unused, reserved for future Xkntt |

## 3. Instruction formats

### R-type

```
 31        25 24     20 19     15 14   12 11      7 6            0
+------------+---------+---------+-------+---------+--------------+
|   funct7   |   rs2   |   rs1   |funct3 |    rd   |    opcode    |
+------------+---------+---------+-------+---------+--------------+
      7           5         5        3        5           7
```

### R4-type

Standard RISC-V R4, the format already reserved for FMA-class instructions.
Chosen over an implicit zeta register because it is the architecturally correct
answer and LLVM has `RVInstR4` ready to use (plan §3). The cost is a **third
register-file read port**, which is a deliberate microarchitectural commitment
(see [§7](#7-implementation-notes)).

```
 31    27 26  25 24     20 19     15 14   12 11      7 6            0
+--------+------+---------+---------+-------+---------+--------------+
|  rs3   |funct2|   rs2   |   rs1   |funct3 |    rd   |    opcode    |
+--------+------+---------+---------+-------+---------+--------------+
    5       2        5         5        3        5           7
```

### Decode rules

1. **Within `custom-0`, `funct3` selects the format as well as the operation.**
   `funct3` ∈ {3, 4} are R4-type; all other values are R-type. A decoder MUST
   apply this rule before interpreting bits 31:25.
2. All `custom-1` instructions use the R-type shape.
3. **Reserved encoding fields MUST be zero.** A register field an instruction
   does not use is reserved; a nonzero value is an **illegal instruction**, not
   a don't-care. This gives a crisp legal/illegal boundary, which matters
   because plan A3 requires the RTL decoder to be compared against a Python
   decoder over 10^6 random words — a lax implementation and a strict one would
   disagree on exactly those words.
4. Any `funct3`/`funct7`/`funct2` combination not listed here is **reserved** and
   MUST raise an illegal instruction.

---

## 4. Tier 1 — `custom-0` (0x0B)

Fixed-latency, register-to-register, no memory access. These participate in
hazard detection and forwarding exactly like `ADD`.

### 4.1 `kmm rd, rs1, rs2` — Montgomery multiply

R-type, `funct3 = 0`, `funct7 = 0x00`.

```
rd = sext32( montgomery_reduce( sext16(rs1[15:0]) * sext16(rs2[15:0]) ) )
```

Result is in `(-q, q)`, sign-extended to 32 bits. `rs1[31:16]` and `rs2[31:16]`
are ignored.

Also used for the inverse NTT's final scaling by `f = 1441 = mont^2/128`.

### 4.2 `kbfct rd, rs1, rs2` — packed Cooley–Tukey butterfly

R-type, `funct3 = 1`, `funct7 = 0x00`.

```
a = sext16(rs1[15:0])        # f[j]
b = sext16(rs1[31:16])       # f[j+len]
z = sext16(rs2[15:0])        # zeta, Montgomery domain
t = montgomery_reduce(z * b)
rd = { (a - t)[15:0], (a + t)[15:0] }     # hi = f[j+len]', lo = f[j]'
```

This is exactly the reference inner loop:

```c
t = fqmul(zeta, r[j + len]);
r[j + len] = r[j] - t;
r[j]       = r[j] + t;
```

### 4.3 `kbfgs rd, rs1, rs2` — packed Gentleman–Sande butterfly

R-type, `funct3 = 2`, `funct7 = 0x00`.

```
a = sext16(rs1[15:0])        # f[j]
b = sext16(rs1[31:16])       # f[j+len]
z = sext16(rs2[15:0])
a' = barrett_reduce( (a + b)[15:0] )
b' = montgomery_reduce( z * (b - a)[15:0] )
rd = { b'[15:0], a'[15:0] }
```

> **The operand order of the subtraction is `(b - a)`, not `(a - b)`.** See
> [Deviation 1](#deviation-1-kbfgs-subtraction-order). Getting this backwards
> negates every second output and makes INTT wrong.

Reference inner loop:

```c
t          = r[j];
r[j]       = barrett_reduce(t + r[j + len]);
r[j + len] = r[j + len] - t;                  /* = b - a */
r[j + len] = fqmul(zeta, r[j + len]);
```

### 4.4 `kbmul0 rd, rs1, rs2, rs3` — base multiply, coefficient 0

**R4-type**, `funct3 = 3`, `funct2 = 0b00`.

```
a0 = sext16(rs1[15:0]),  a1 = sext16(rs1[31:16])
b0 = sext16(rs2[15:0]),  b1 = sext16(rs2[31:16])
z  = sext16(rs3[15:0])                       # +zeta or -zeta
t  = montgomery_reduce(a1 * b1)
t  = montgomery_reduce(t * z)
rd = sext32( (t + montgomery_reduce(a0 * b0))[15:0] )
```

Computes `c0 = a0*b0 + a1*b1*z` for `(a0 + a1*X)(b0 + b1*X) mod (X^2 - z)`.
Consecutive coefficient pairs use `+z` and `-z`; the sign is the caller's
responsibility (it comes from `poly_basemul_montgomery`).

### 4.5 `kmac rd, rs1, rs2, rs3` — modular multiply-accumulate

**R4-type**, `funct3 = 4`, `funct2 = 0b00`.

```
rd = sext32( barrett_reduce( ( sext16(rs3[15:0])
                             + montgomery_reduce(sext16(rs1[15:0])
                                               * sext16(rs2[15:0])) )[15:0] ) )
```

R4 rather than reading `rd`, so every Tier-1 instruction is non-destructive and
the forwarding logic needs no special case (plan §3).

**Software constraint:** the addend and the product are summed and then
truncated to 16 bits before `barrett_reduce`. The truncation is lossless only
while `|rs3| + q < 32768`, i.e. `|rs3| < 29439`. Exceeding that is not an
exception — the result simply wraps, matching what the equivalent C would do.

### 4.6 `kbmul1 rd, rs1, rs2` — base multiply, coefficient 1

R-type, `funct3 = 5`, `funct7 = 0x00`.

```
a0 = sext16(rs1[15:0]),  a1 = sext16(rs1[31:16])
b0 = sext16(rs2[15:0]),  b1 = sext16(rs2[31:16])
rd = sext32( ( montgomery_reduce(a0 * b1)
             + montgomery_reduce(a1 * b0) )[15:0] )
```

Computes `c1 = a0*b1 + a1*b0`. **R-type, not R4:** `c1` does not involve zeta,
so encoding an unused `rs3` would burn a read port and raise the question of
whether a nonzero `rs3` is legal. See
[Deviation 2](#deviation-2-kbmul1-added).

---

## 5. Tier 2 — `custom-1` (0x2B)

The control plane for the block coprocessor. The *data* plane is memory-mapped
(plan §1.1): bulk movement uses ordinary `lw`/`sw` against the coprocessor's
aperture. These instructions only launch and observe.

All Tier-2 instructions use the R-type shape with reserved register fields.

### 5.1 Mode word

Passed in `rs2` of `kntt.cfg` and in `rs1` of `kntt.start`.

| Bits | Name | Meaning |
|---|---|---|
| 1:0 | `OP` | `0` = forward NTT, `1` = inverse NTT, `2` = basemul, `3` = reserved |
| 2 | `SRC` | source buffer (`0` = A, `1` = B) |
| 3 | `DST` | destination buffer |
| 31:4 | — | reserved, MUST be zero |

### 5.2 Status word

Returned by `kntt.stat` and `kntt.wait`.

| Bits | Name | Meaning |
|---|---|---|
| 0 | `BUSY` | an operation is in flight |
| 1 | `DONE` | the last operation completed |
| 2 | `ERR` | invalid mode, or `kntt.start` issued while busy |
| 15:3 | — | reserved, read as zero |
| 31:16 | `CYCLES` | cycles taken by the last completed operation, saturating at `0xFFFF` |

`CYCLES` is free benchmarking (plan B7) and is the measurement used for the
constant-time assertion in B8.

### 5.3 `kntt.cfg rs1, rs2`

`funct3 = 0`, `funct7 = 0x00`. **`rd` is reserved and MUST be zero.**

Latches `rs1` as the operand descriptor (base indices / bank select) and `rs2`
as the mode word. Has no effect on a running operation. If the mode word has
reserved bits set, `ERR` is raised and the configuration is not latched.

### 5.4 `kntt.start rd, rs1`

`funct3 = 1`, `funct7 = 0x00`. **`rs2` is reserved and MUST be zero.**

Launches the operation described by `rs1` (mode word). Writes a **nonzero**
token to `rd` if accepted, or `0` if the unit was busy or the mode was invalid.

> Software MUST test for *nonzero*, not for `== 1`. Version 0.1 always returns
> `1` on acceptance; a later version may return a sequence number.

Issuing `kntt.start` while `BUSY` does **not** trap: it sets `ERR`, returns `0`,
and leaves the running operation undisturbed.

### 5.5 `kntt.wait rd`

`funct3 = 2`, `funct7 = 0x00`. **`rs1` and `rs2` are reserved and MUST be zero.**

Stalls the pipeline until the unit is idle, then writes the status word to `rd`.
If the unit is already idle, it returns immediately with the current status —
this is **not** an error.

Stalling is the correct first implementation (plan §3). A polling variant that
lets the core do useful work concurrently is a later, measurable refinement, and
`kntt.stat` already exists to support it.

### 5.6 `kntt.stat rd`

`funct3 = 3`, `funct7 = 0x00`. **`rs1` and `rs2` are reserved and MUST be zero.**

Non-blocking read of the status word into `rd`. Never stalls, never traps.

### 5.7 Coprocessor RAM access while busy

A load or store to the coprocessor aperture while `BUSY` **stalls until the
operation completes**. It MUST NOT return stale data.

This is the simple, obviously-correct choice. Returning an error flag instead
would be faster but puts a silent-wrong-answer path into the design; plan I2
requires that stale data never be returned silently, and a stall guarantees it
by construction.

---

## 6. Encoding summary

Canonical instance of every instruction, with `t0 = x5`, `t1 = x6`, `t2 = x7`,
`t3 = x28`. **These hex values are parsed out of this table by
`tb/unit/test_isa_encoding.py`** and checked against the executable model and
against GNU `as`.

| Instruction | Format | funct3 | funct7 / funct2 | Encoding |
|---|---|---|---|---|
| `kmm t0, t1, t2` | R | 0 | `0x00` | `0x0073028B` |
| `kbfct t0, t1, t2` | R | 1 | `0x00` | `0x0073128B` |
| `kbfgs t0, t1, t2` | R | 2 | `0x00` | `0x0073228B` |
| `kbmul0 t0, t1, t2, t3` | R4 | 3 | `0b00` | `0xE073328B` |
| `kmac t0, t1, t2, t3` | R4 | 4 | `0b00` | `0xE073428B` |
| `kbmul1 t0, t1, t2` | R | 5 | `0x00` | `0x0073528B` |
| `kntt.cfg t1, t2` | R | 0 | `0x00` | `0x0073002B` |
| `kntt.start t0, t1` | R | 1 | `0x00` | `0x000312AB` |
| `kntt.wait t0` | R | 2 | `0x00` | `0x000022AB` |
| `kntt.stat t0` | R | 3 | `0x00` | `0x000032AB` |

### 6.1 Worked encoding: `kmm t0, t1, t2`

Fields: `rd = t0 = x5`, `rs1 = t1 = x6`, `rs2 = t2 = x7`, `funct3 = 0`,
`funct7 = 0x00`, `opcode = 0x0B`.

```
funct7   rs2     rs1    funct3   rd     opcode
0000000  00111  00110    000   00101  0001011
```

Concatenated and split into bytes:

```
00000000 01110011 00000010 10001011
   0x00     0x73     0x02     0x8B
```

→ **`0x0073028B`**, which is what GNU `as` emits for
`.insn r 0x0B, 0, 0x00, t0, t1, t2`.

### 6.2 Worked encoding: `kbmul0 t0, t1, t2, t3`

R4 fields: `rs3 = t3 = x28 = 11100`, `funct2 = 00`, `rs2 = x7 = 00111`,
`rs1 = x6 = 00110`, `funct3 = 3 = 011`, `rd = x5 = 00101`, `opcode = 0001011`.

```
rs3   f2    rs2     rs1   funct3   rd     opcode
11100 00  00111   00110    011   00101  0001011
```

```
11100000 01110011 00110010 10001011
   0xE0     0x73     0x32     0x8B
```

→ **`0xE073328B`**.

### 6.3 Hand decoding

The plan's done-condition requires decoding these words back to mnemonics
*without* consulting the table above. Extract fields by position:

**`0x0073228B`** → `0000 0000 0111 0011 0010 0010 1000 1011`

| Field | Bits | Value | Meaning |
|---|---|---|---|
| `opcode` | 6:0 | `0001011` = `0x0B` | `custom-0`, so Tier 1 |
| `rd` | 11:7 | `00101` = 5 | `t0` |
| `funct3` | 14:12 | `010` = 2 | not in {3,4} → **R-type** |
| `rs1` | 19:15 | `00110` = 6 | `t1` |
| `rs2` | 24:20 | `00111` = 7 | `t2` |
| `funct7` | 31:25 | `0000000` | `0x00` |

`custom-0` + R-type + `funct3 = 2` → **`kbfgs t0, t1, t2`**. ✓

**`0xE073428B`** → `1110 0000 0111 0011 0100 0010 1000 1011`

| Field | Bits | Value | Meaning |
|---|---|---|---|
| `opcode` | 6:0 | `0001011` | `custom-0` |
| `rd` | 11:7 | `00101` = 5 | `t0` |
| `funct3` | 14:12 | `100` = 4 | **in {3,4} → R4-type** |
| `rs1` | 19:15 | `00110` = 6 | `t1` |
| `rs2` | 24:20 | `00111` = 7 | `t2` |
| `funct2` | 26:25 | `00` | |
| `rs3` | 31:27 | `11100` = 28 | `t3` |

`custom-0` + R4 + `funct3 = 4` → **`kmac t0, t1, t2, t3`**. ✓

**`0x000022AB`** → `0000 0000 0000 0000 0010 0010 1010 1011`

`opcode = 0101011 = 0x2B` → `custom-1`, Tier 2. `funct3 = 010 = 2`,
`rd = 00101 = 5`, `rs1 = 0`, `rs2 = 0`, `funct7 = 0`. Both source fields are
zero as required → **`kntt.wait t0`**. ✓

---

## 7. Implementation notes

### Latency contract

Semantics are frozen; **latency is a performance contract** and is the one part
of this document expected to change. When it does, it MUST change in three
places together: the RTL, Spike's timing model, and the LLVM `SchedMachineModel`
(plan C4). A stale scheduling model produces code that stalls on real hardware.

| Instruction | Latency (cycles) | Rationale |
|---|---|---|
| `kmm` | 4 | one pipelined Montgomery multiply (plan B1) |
| `kbfct` | 5 | modmul + add/sub |
| `kbfgs` | 5 | modmul + sub; the Barrett path is shorter |
| `kbmul0` | 9 | **two chained** Montgomery multiplies, then an add |
| `kmac` | 6 | modmul + add + Barrett |
| `kbmul1` | 5 | two parallel modmuls + add |
| `kntt.cfg` / `kntt.start` / `kntt.stat` | 1 | register write only |
| `kntt.wait` | variable | blocks until idle |

All Tier-1 instructions are fully pipelined (II = 1). `kbmul0` is the outlier at
9 cycles because `c0` requires `fqmul(fqmul(a1,b1), zeta)` — two dependent
reductions. If this proves to be a scheduling problem, the fix is to widen the
unit, not to change the semantics.

### Exception behaviour

| Condition | Result |
|---|---|
| Extension not enabled in `misa`/ISA string | illegal instruction (`mcause = 2`) |
| Reserved encoding field nonzero | illegal instruction |
| Unlisted `funct3`/`funct7`/`funct2` | illegal instruction |
| `kntt.start` while `BUSY` | **no trap**; `ERR` set, `rd = 0` |
| Invalid mode word | **no trap**; `ERR` set, `rd = 0` |
| `kntt.wait` while idle | **no trap**; returns current status |
| `kmac` addend out of range | **no trap**; result wraps (see §4.5) |

No Xkntt instruction accesses memory, so none can raise a load/store fault.
Tier-2 instructions deliberately report errors through status bits rather than
traps: an accelerator control path that traps is far harder to use from a
polling loop, and the plan's B8 constant-time requirement is easier to hold when
there are no data-dependent control-flow exits.

### Register-file pressure

R4-type requires a **third read port**. On Artix-7 a 3-read-port register file
is built as replicated banks and costs more than one might expect (plan I1).
This is a deliberate commitment made here so that Track A can plan for it from
the start rather than discovering it at integration.

### The `.insn` bridge

Until the LLVM backend exists (C2), every instruction can be emitted with stock
tools, so RTL bring-up is not blocked on the toolchain:

```c
#define KBFCT(rd, rs1, rs2) \
    asm volatile(".insn r 0x0B, 1, 0x00, %0, %1, %2" \
                 : "=r"(rd) : "r"(rs1), "r"(rs2))

#define KBMUL0(rd, rs1, rs2, rs3) \
    asm volatile(".insn r4 0x0B, 3, 0x00, %0, %1, %2, %3" \
                 : "=r"(rd) : "r"(rs1), "r"(rs2), "r"(rs3))
```

Verified: GNU `as` produces exactly the encodings in §6.

---

## 8. Deviations from the development plan

The plan's §3 table is explicitly a *proposal*. Three changes were made, each
with evidence.

### Deviation 1: `kbfgs` subtraction order

**The plan's proposed semantics are wrong.** It states:

> `t = a`; `a' = barrett_reduce(b + t)`; `b' = montgomery_reduce(z * (t - b))`

`t - b` is `a - b`. The pq-crystals reference computes
`fqmul(zeta, r[j+len] - r[j])`, which is `b - a` — the negation.

Implemented as `(b - a)` to match the reference, per plan §1.3's instruction to
match bit-for-bit. This was not a judgement call: implementing the plan's
literal text and running `tb/unit/test_isa_semantics.py` fails immediately with

```
FAIL: trial 0: invntt via kbfgs differs at coeff 2: 1369 != -1369
```

an exact negation, on the first random polynomial.

### Deviation 2: `kbmul1` added

The plan lists `kbmul0` ("base-case multiply, coefficient 0") but no instruction
for `c1`. `basemul` needs both:

```
c0 = a0*b0 + a1*b1*z        <- kbmul0
c1 = a0*b1 + a1*b0          <- kbmul1
```

Without `c1` the coprocessor cannot perform the operation it exists for.
`kbmul1` takes `funct3 = 5` so that `kmac` keeps the plan's `funct3 = 4`.

It is **R-type, not R4**, because `c1` does not involve zeta. Encoding an unused
`rs3` would consume a third read port for nothing and would require deciding
whether a nonzero `rs3` is legal.

### Deviation 3: reserved fields are strict

The plan does not say what an unused register field means. This document
requires zero and makes nonzero an illegal instruction, rather than "ignored".

Rationale: plan A3 requires comparing the RTL decoder against a Python decoder
over 10^6 random words. A strict rule and a lax rule disagree on precisely those
words, so the choice must be explicit or the two implementations will diverge on
random-word testing and the cause will be non-obvious.

### Accepted as proposed

Both of the plan's stated recommendations are adopted unchanged: **R4-type for
`kbmul0`**, and **R4-type for `kmac`** rather than a destructive `rd` read.

---

## 9. Deferred

Out of scope for version 0.1, recorded so they are not silently forgotten:

- **Detailed Tier-2 register map** — the aperture layout and offsets are Track B
  (plan B7). §5 fixes only the mode and status words, which is what the ISA-level
  contract needs.
- **`kntt.cfg` operand descriptor layout** (`rs1`) — depends on the coprocessor's
  buffer organisation, decided in B3.
- **DMA** — plan M2 identifies data movement as the biggest threat to the
  headline speedup. Any DMA instruction is a version 0.2 concern.
- **Plantard reduction** — plan §1.3 says start with Montgomery, then swap the
  reduction unit behind a verified interface. That swap must not change any
  encoding in this document.
