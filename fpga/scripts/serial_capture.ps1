# Capture the Arty's USB-UART to a file, optionally sending a byte first.
#
# This exists so the hardware loop needs no human: the agent side cannot open a
# COM port from WSL (the FT2232 is not attached to WSL by usbipd), but it CAN
# run this on the Windows side and then read the resulting file through /mnt/c.
#
# -SendByte injects one byte into the FPGA's receiver, which is the only way to
# exercise the RX path; it is sent AFTER the port settles so it cannot be eaten
# by the open.
param(
  [string]$Port    = "COM7",
  [int]   $Baud    = 115200,
  [string]$Out     = "C:\Users\liamf\rvntt-hw\uart.log",
  [int]   $Seconds = 8,
  [int]   $SendByte = -1
)
$ErrorActionPreference = "Stop"
$sp = New-Object System.IO.Ports.SerialPort $Port,$Baud,'None',8,'one'
$sp.ReadTimeout  = 250
$sp.WriteTimeout = 250
# The FT2232 asserts these on open; the Arty ignores them, but some terminals
# leave DTR low and then nothing is received at all.
$sp.DtrEnable = $true
$sp.RtsEnable = $true
$sp.Open()
Start-Sleep -Milliseconds 200
$sp.DiscardInBuffer()

if ($SendByte -ge 0) {
  Start-Sleep -Milliseconds 300
  $sp.Write([byte[]]@($SendByte), 0, 1)
}

$sw = New-Object System.IO.StreamWriter($Out, $false)
$deadline = (Get-Date).AddSeconds($Seconds)
$total = 0
while ((Get-Date) -lt $deadline) {
  try {
    $c = $sp.ReadExisting()
    if ($c.Length -gt 0) { $sw.Write($c); $sw.Flush(); $total += $c.Length }
  } catch { }
  Start-Sleep -Milliseconds 25
}
$sw.Close()
$sp.Close()
Write-Output "CAPTURE_DONE bytes=$total file=$Out"
