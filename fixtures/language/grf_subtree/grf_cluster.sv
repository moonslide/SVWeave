// CORES cores fed by one cluster input; their outputs meet in one reducer.
module grf_cluster #(
    parameter int W     = 8,
    parameter int CORES = 2
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [W-1:0] cin,
    output logic [W-1:0] cout
);
  logic [CORES*W-1:0] core_bus;
  for (genvar i = 0; i < CORES; i++) begin : g_core
    grf_core #(.W(W)) u_core (.clk(clk), .rst_n(rst_n), .din(cin), .dout(core_bus[i*W+:W]));
  end
  grf_reduce #(.W(W), .N(CORES)) u_red (.clk(clk), .rst_n(rst_n), .din(core_bus), .dout(cout));
endmodule
