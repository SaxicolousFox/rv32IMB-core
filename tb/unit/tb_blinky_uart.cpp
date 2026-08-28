// Decodes the UART line out of rvntt_blinky_top and checks the exact banner.
//
// Doing this in simulation means the only things left to fail on the board are
// genuinely physical: pin assignment, MMCM, and the USB-UART bridge.  Debugging
// a wrong string over a serial cable is far more expensive than here.
#include "Vrvntt_blinky_top.h"
#include "verilated.h"
#include <cstdio>
#include <string>

// Must match rvntt_uart_tx: CLK_HZ / BAUD = 75e6 / 115200 = 651.
static const int BIT_CYCLES = 651;

int main(int argc, char** argv) {
    Verilated::commandArgs(argc, argv);
    Vrvntt_blinky_top* dut = new Vrvntt_blinky_top;

    std::string got;
    // UART receiver state
    int  state = 0;        // 0 = idle, 1 = receiving
    int  countdown = 0;
    int  bit_i = 0;
    unsigned ch = 0;
    int  prev_tx = 1;

    dut->ck_rst = 0;       // active-low reset asserted
    dut->CLK100MHZ = 0;
    dut->eval();

    const long MAX_CYCLES = 900000;
    for (long c = 0; c < MAX_CYCLES; c++) {
        if (c == 50) dut->ck_rst = 1;          // release reset

        dut->CLK100MHZ = 0; dut->eval();
        dut->CLK100MHZ = 1; dut->eval();

        int tx = dut->uart_rxd_out & 1;

        if (state == 0) {
            // look for the start bit (line goes low)
            if (prev_tx == 1 && tx == 0) {
                state = 1;
                countdown = BIT_CYCLES + BIT_CYCLES / 2;  // sample mid-bit-0
                bit_i = 0; ch = 0;
            }
        } else {
            if (--countdown == 0) {
                if (bit_i < 8) {
                    ch |= (unsigned)(tx & 1) << bit_i;
                    bit_i++;
                    countdown = BIT_CYCLES;
                } else {
                    // stop bit position; accept the byte
                    got.push_back((char)(ch & 0xFF));
                    state = 0;
                }
            }
        }
        prev_tx = tx;

        if (got.size() >= 43) break;
    }
    delete dut;

    const std::string want = "rvntt P0.5 clk=75MHz bram=0xD76C0E8D PASS\r\n";

    // print with CR/LF escaped so the comparison is readable
    std::string shown;
    for (char x : got) {
        if (x == '\r') shown += "\\r";
        else if (x == '\n') shown += "\\n";
        else shown += x;
    }
    printf("UART decoded: \"%s\"\n", shown.c_str());

    if (got == want) { printf("BLINKY_UART_TB_OK\n"); return 0; }
    printf("BLINKY_UART_TB_FAIL: expected \"rvntt P0.5 clk=75MHz bram=0xD76C0E8D PASS\\r\\n\"\n");
    return 1;
}
