// Registered XOR reduction of N lanes: the fan-in point of a cluster or of the SoC.
module grf_reduce #(
    parameter int W = 8,
    parameter int N = 2
) (
    input  logic           clk,
    input  logic           rst_n,
    input  logic [N*W-1:0] din,
    output logic [W-1:0]   dout
);
  logic [W-1:0] acc;
  always_comb begin
    acc = '0;
    for (int i = 0; i < N; i++) acc ^= din[i*W+:W];
  end
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) dout <= '0;
    else dout <= acc;
endmodule
