// One registered pipeline stage (leaf of the GRF subtree-export fixture).
module grf_stage #(
    parameter int W = 8,
    parameter int K = 1
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [W-1:0] d,
    output logic [W-1:0] q
);
  always_ff @(posedge clk or negedge rst_n)
    if (!rst_n) q <= '0;
    else q <= d + W'(K);
endmodule
