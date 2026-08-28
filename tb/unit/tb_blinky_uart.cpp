// Decodes the UART line out of rvntt_blinky_top and checks both the exact
// banner AND the interval between repeats.
//
// The interval check exists because its absence let a real bug reach hardware:
// the report FSM used a hardcoded 27-bit counter compared against CLK_HZ/16,
// so it emitted ~15 lines/second while the comment claimed one.  A testbench
// that stops after the first message cannot see that.  Anything the design
// promises should be checked, including its timing.
#include "Vrvntt_blinky_top.h"
#include "verilated.h"
#include <cstdio>
#include <cstdlib>
#include <string>

static const int  BIT_CYCLES = 651;          // CLK_HZ / BAUD = 75e6 / 115200
static const int  CLK_HZ     = 75000000;
static const int  MSG_LEN    = 43;

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Vrvntt_blinky_top* dut = new Vrvntt_blinky_top;

    std::string got;
    std::string first_msg;
    long msg_start_cycle[2] = {-1, -1};
    int  msgs_seen = 0;

    int state = 0, countdown = 0, bit_i = 0, prev_tx = 1;
    unsigned ch = 0;
    int byte_in_msg = 0;

    dut->ck_rst = 0;
    dut->CLK100MHZ = 0;
    dut->eval();

    // Two messages plus the one-second gap between them.
    const long MAX_CYCLES = (long)CLK_HZ * 3;
    long c;
    for (c = 0; c < MAX_CYCLES; c++) {
        if (c == 50) dut->ck_rst = 1;

        dut->CLK100MHZ = 0; dut->eval();
        dut->CLK100MHZ = 1; dut->eval();

        int tx = dut->uart_rxd_out & 1;

        if (state == 0) {
            if (prev_tx == 1 && tx == 0) {           // start bit
                if (byte_in_msg == 0) {              // first char of a message
                    if (msgs_seen < 2) msg_start_cycle[msgs_seen] = c;
                    msgs_seen++;
                }
                state = 1;
                countdown = BIT_CYCLES + BIT_CYCLES / 2;
                bit_i = 0; ch = 0;
            }
        } else if (--countdown == 0) {
            if (bit_i < 8) {
                ch |= (unsigned)(tx & 1) << bit_i;
                bit_i++;
                countdown = BIT_CYCLES;
            } else {
                char byte = (char)(ch & 0xFF);
                if (msgs_seen == 1) got.push_back(byte);
                byte_in_msg++;
                if (byte_in_msg == MSG_LEN) {
                    if (first_msg.empty()) first_msg = got;
                    byte_in_msg = 0;
                }
                state = 0;
            }
        }
        prev_tx = tx;

        if (msgs_seen >= 2 && msg_start_cycle[1] >= 0) break;
    }
    delete dut;

    int failures = 0;
    const std::string want = "rvntt P0.5 clk=75MHz bram=0xD76C0E8D PASS\r\n";

    std::string shown;
    for (char x : first_msg) {
        if (x == '\r') shown += "\\r"; else if (x == '\n') shown += "\\n"; else shown += x;
    }
    printf("UART decoded: \"%s\"\n", shown.c_str());

    if (first_msg != want) {
        printf("  FAIL: banner mismatch\n");
        failures++;
    }

    if (msg_start_cycle[0] < 0 || msg_start_cycle[1] < 0) {
        printf("  FAIL: saw only %d message start(s) in %ld cycles\n", msgs_seen, c);
        failures++;
    } else {
        long period = msg_start_cycle[1] - msg_start_cycle[0];
        double secs = (double)period / CLK_HZ;
        printf("repeat period: %ld cycles = %.4f s (%.2f lines/sec)\n",
               period, secs, 1.0 / secs);
        // One line per second, within 2%.
        if (secs < 0.98 || secs > 1.02) {
            printf("  FAIL: expected ~1.000 s between lines, got %.4f s\n", secs);
            failures++;
        }
    }

    if (failures) { printf("BLINKY_UART_TB_FAIL (%d)\n", failures); return 1; }
    printf("BLINKY_UART_TB_OK\n");
    return 0;
}
