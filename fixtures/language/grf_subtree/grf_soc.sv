// Top of the GRF subtree-export fixture: CLUSTERS x CORES x 4 stages.
// Instances: 1 + CLUSTERS * (2 + 5 * CORES) + 1.
module grf_soc #(
    parameter int W        = 8,
    parameter int CLUSTERS = 2,
    parameter int CORES    = 2
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [W-1:0] din,
    output logic [W-1:0] dout
);
  logic [CLUSTERS*W-1:0] cluster_bus;
  for (genvar c = 0; c < CLUSTERS; c++) begin : g_cl
    grf_cluster #(.W(W), .CORES(CORES)) u_cl (
        .clk(clk), .rst_n(rst_n), .cin(din), .cout(cluster_bus[c*W+:W]));
  end
  grf_reduce #(.W(W), .N(CLUSTERS)) u_red (
      .clk(clk), .rst_n(rst_n), .din(cluster_bus), .dout(dout));
endmodule
