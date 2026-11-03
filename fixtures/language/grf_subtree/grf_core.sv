// Four-stage core: fetch -> decode -> exec -> writeback, chained through named core signals.
module grf_core #(
    parameter int W = 8
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [W-1:0] din,
    output logic [W-1:0] dout
);
  logic [W-1:0] f2d, d2e, e2w;
  grf_stage #(.W(W), .K(1)) u_fetch  (.clk(clk), .rst_n(rst_n), .d(din), .q(f2d));
  grf_stage #(.W(W), .K(2)) u_decode (.clk(clk), .rst_n(rst_n), .d(f2d), .q(d2e));
  grf_stage #(.W(W), .K(3)) u_exec   (.clk(clk), .rst_n(rst_n), .d(d2e), .q(e2w));
  grf_stage #(.W(W), .K(4)) u_wb     (.clk(clk), .rst_n(rst_n), .d(e2w), .q(dout));
endmodule
