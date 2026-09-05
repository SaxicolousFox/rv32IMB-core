// Drives rvntt_soc_top (via rvntt_soc_sim_top) and checks the whole A12
// done-when path in simulation: a program in BRAM runs, and its output arrives
// over the UART.
//
// It checks more than the banner, on the P0.5 principle that a testbench which
// stops at the first correct-looking character cannot see a design that is
// right once and wrong afterwards:
//
//   * the exact block contents, including sw/btn read back through GPIO_IN;
//   * that the block REPEATS and that `iter` increments, so a program that
//     printed one block and hung is not a pass;
//   * that a byte sent INTO uart_txd_in comes back in `echo`, which is the only
//     test of the receive path;
//   * the RGB status LEDs: heartbeat toggling, alive set, error clear.
#include "Vrvntt_soc_sim_top.h"
#include "verilated.h"
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

static const int BIT_CYCLES = 34;     // CORE_HZ / BAUD = 4e6 / 115200 -> 34

// The host deliberately transmits ONE CYCLE PER BIT SLOWER than the receiver's
// divisor -- a ~2.9% baud mismatch, which is about what a real FTDI plus an
// integer divisor gives you, and just inside the ~2% per-bit-time budget a UART
// is supposed to absorb over a 10-bit frame.
//
// This is not decoration.  With a perfectly matched host, a receiver that
// samples on the bit BOUNDARY decodes exactly as well as one that samples at the
// MIDPOINT, so the mid-bit sampling that makes rvntt_uart_rx work on real
// hardware is untestable -- and a mutation that moves the sample point escapes.
// The skew is what gives the midpoint something to be right about.
static const int HOST_BIT_CYCLES = BIT_CYCLES + 1;
static const long MAX_CYCLES = 4000000;

// Host -> FPGA byte, sent once the first block has been seen so that the reply
// is guaranteed to land in the SECOND block rather than racing the first.
static const unsigned char TX_BYTE = 0x5A;

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Vrvntt_soc_sim_top* dut = new Vrvntt_soc_sim_top;

    std::string rx;                    // everything decoded off uart_rxd_out
    int  state = 0, countdown = 0, bit_i = 0, prev_tx = 1;
    unsigned ch = 0;

    // Host transmitter state.
    int  tx_state = -1, tx_count = 0, tx_bit = 0;

    int  hb_edges = 0, prev_hb = 0;
    bool saw_err = false, saw_alive = false;
    std::vector<unsigned> led_seen;

    dut->ck_rst      = 0;
    dut->CLK100MHZ   = 0;
    dut->uart_txd_in = 1;              // idle high
    dut->sw          = 0xA;
    dut->btn         = 0x5;
    dut->eval();

    long c;
    int blocks = 0;
    for (c = 0; c < MAX_CYCLES; c++) {
        if (c == 50) dut->ck_rst = 1;

        dut->CLK100MHZ = 0; dut->eval();
        dut->CLK100MHZ = 1; dut->eval();

        if ((int)(dut->led0_g & 1) != prev_hb) { hb_edges++; prev_hb = dut->led0_g & 1; }
        if (dut->led0_r & 1) saw_err = true;
        if (dut->led0_b & 1) saw_alive = true;
        if (led_seen.empty() || led_seen.back() != (unsigned)dut->led)
            led_seen.push_back(dut->led);

        // ---- host -> FPGA -------------------------------------------------
        if (tx_state >= 0) {
            if (--tx_count <= 0) {
                tx_count = HOST_BIT_CYCLES;
                if (tx_bit == 0)                 dut->uart_txd_in = 0;            // start
                else if (tx_bit <= 8)            dut->uart_txd_in = (TX_BYTE >> (tx_bit - 1)) & 1;
                else if (tx_bit == 9)            dut->uart_txd_in = 1;            // stop
                else { tx_state = -1; dut->uart_txd_in = 1; }
                tx_bit++;
            }
        }

        // ---- FPGA -> host -------------------------------------------------
        int tx = dut->uart_rxd_out & 1;
        if (state == 0) {
            if (prev_tx == 1 && tx == 0) {
                state = 1;
                countdown = BIT_CYCLES + BIT_CYCLES / 2;   // sample mid-bit
                bit_i = 0; ch = 0;
            }
        } else if (--countdown == 0) {
            if (bit_i < 8) {
                ch |= (unsigned)(tx & 1) << bit_i;
                bit_i++;
                countdown = BIT_CYCLES;
            } else {
                rx.push_back((char)(ch & 0xFF));
                state = 0;
                if (rx.size() >= 13 &&
                    rx.compare(rx.size() - 13, 13, "=== end ===\r\n") == 0) {
                    blocks++;
                    if (blocks == 1) { tx_state = 0; tx_count = 1; tx_bit = 0; }
                    if (blocks == 3) break;
                }
            }
        }
        prev_tx = tx;
    }
    delete dut;

    // ---- split into blocks -------------------------------------------------
    std::vector<std::string> blk;
    size_t pos = 0;
    while (true) {
        size_t s = rx.find("=== rvntt A12 ===\r\n", pos);
        if (s == std::string::npos) break;
        size_t e = rx.find("=== end ===\r\n", s);
        if (e == std::string::npos) break;
        blk.push_back(rx.substr(s, e + 13 - s));
        pos = e + 13;
    }

    int failures = 0;
    printf("cycles=%ld  blocks=%zu  hb_edges=%d\n", c, blk.size(), hb_edges);
    for (size_t i = 0; i < blk.size() && i < 3; i++) {
        std::string shown = blk[i];
        std::string out;
        for (char x : shown) { if (x == '\r') out += "\\r"; else if (x == '\n') out += "\\n\n  "; else out += x; }
        printf("--- block %zu ---\n  %s\n", i, out.c_str());
    }

    if (blk.size() < 2) {
        printf("  FAIL: need at least 2 complete blocks, got %zu\n", blk.size());
        failures++;
    }

    if (!blk.empty()) {
        const char* want_hello = "hello=Hello, world!\r\n";
        if (blk[0].find(want_hello) == std::string::npos) {
            printf("  FAIL: no \"Hello, world!\" line in block 0\n"); failures++;
        }
        // GPIO_IN read back through the CPU: sw=0xA btn=0x5 were driven above.
        if (blk[0].find("sw=0xA btn=0x5\r\n") == std::string::npos) {
            printf("  FAIL: GPIO_IN readback wrong (expected sw=0xA btn=0x5)\n"); failures++;
        }
        if (blk[0].find("mcycle=0x") == std::string::npos ||
            blk[0].find("minstret=0x") == std::string::npos) {
            printf("  FAIL: missing mcycle/minstret line\n"); failures++;
        }
    }

    // iter must advance: a program that printed one block and looped on the
    // same value would otherwise pass everything above.
    if (blk.size() >= 2) {
        unsigned it0 = 0, it1 = 0;
        size_t a = blk[0].find("iter=0x"), b = blk[1].find("iter=0x");
        if (a == std::string::npos || b == std::string::npos) {
            printf("  FAIL: missing iter= line\n"); failures++;
        } else {
            sscanf(blk[0].c_str() + a + 7, "%8x", &it0);
            sscanf(blk[1].c_str() + b + 7, "%8x", &it1);
            printf("iter: %u -> %u\n", it0, it1);
            if (it1 != it0 + 1) {
                printf("  FAIL: iter did not advance by one\n"); failures++;
            }
        }
    }

    // The receive path.  The byte was injected after block 0, so it must show
    // up in block 1 or 2 -- and block 0 must NOT already claim to have it.
    bool echoed = false;
    char want_echo[32];
    snprintf(want_echo, sizeof(want_echo), "echo=0x%02X\r\n", TX_BYTE);
    for (size_t i = 1; i < blk.size(); i++)
        if (blk[i].find(want_echo) != std::string::npos) echoed = true;
    if (!echoed) {
        printf("  FAIL: injected byte 0x%02X never came back (%s)\n",
               TX_BYTE, "UART RX path");
        failures++;
    }
    if (!blk.empty() && blk[0].find("echo=none") == std::string::npos) {
        printf("  FAIL: block 0 should report echo=none, nothing was sent yet\n");
        failures++;
    }

    // The program's own image check.  An MMIO store that is not gated out of the
    // RAM aliases onto word 0 of .text.init -- crt0, which runs once and is
    // never revisited, so nothing else in this testbench can see it happen.
    for (size_t i = 0; i < blk.size(); i++) {
        if (blk[i].find("img=OK\r\n") == std::string::npos) {
            printf("  FAIL: block %zu does not report img=OK -- the program's "
                   "own image changed under it\n", i);
            failures++;
            break;
        }
    }

    // Status LEDs.
    if (hb_edges < 2)  { printf("  FAIL: heartbeat (led0_g) did not toggle\n"); failures++; }
    if (saw_err)       { printf("  FAIL: led0_r set -- dbg_unsupported fired\n"); failures++; }
    if (!saw_alive)    { printf("  FAIL: led0_b never set -- no instruction retired\n"); failures++; }
    if (led_seen.size() < 3) {
        printf("  FAIL: GPIO_OUT never changed the green LEDs (%zu values)\n",
               led_seen.size());
        failures++;
    }

    if (failures) { printf("SOC_TB_FAIL (%d)\n", failures); return 1; }
    printf("SOC_TB_OK\n");
    return 0;
}
