# What is actually modeled

This is a wiring-constrained spiking-network experiment. The connectome supplies anatomy, not a complete living fly, calibrated physiology, or a game strategy. No LLM selects tiles. No rule overrides the neural proposal with different tiles.

## Anatomy and dynamics

The [MaleCNS v1.0 release](https://male-cns.janelia.org/download/) provides the male brain and ventral nerve cord. Import retains every assigned neuronal superclass, including uncertain classes, while excluding explicit glia and unresolved segmentation objects. It retains all released edges between those entries, including weak edges and self-connections: **166,700 nodes, 25,582,938 directed connections, 124,177,617 synaptic contacts**. “Full retained” describes this inclusion policy; it does not mean every biological synapse was reconstructed.

The importer verifies SHA-256 source files and every compiled graph array against committed locks. Transmitter annotations and cell order are checked too. Neuron IDs remain integers. Source files are downloaded separately under their upstream license.

The native kernel integrates approximate leaky integrate-and-fire cells at **0.1 ms**. It uses 20 ms membrane and 5 ms synaptic time constants, a −45 mV threshold, 1.8 ms transmission delay and 2.2 ms refractory period. Contact count times 0.275 sets initial synaptic magnitude. ACh is assigned excitation; GABA, glutamate and histamine inhibition, with explicit positive fallback for unresolved signs. This is a coarse sign proxy, not receptor-specific physiology. KC rest is −60 mV with an 8 mV adaptation increment decaying over 200 ms; other cells rest at −52 mV.

Pure dopamine, serotonin and octopamine annotations deliver modulatory traces along their retained edges instead of generic fast excitation. Only the specified memory rule consumes selected dopamine activity; most modulatory effects are unmodeled. Cotransmission and receptors remain incomplete. Keeping an edge in the graph does not establish that all its biological effects are reproduced.

The event-driven kernel avoids unnecessary subthreshold updates; it does not prune the graph or enlarge the integration timestep. This accelerates model execution, not biological time.

## What the fly sees

Every round the public SatRush board is rendered locally as a fixed 320×180 image: a 7×3 grid of the 21 tiles, each shaded by the USDC currently staked on it, the last five winning tiles outlined, the pot and miner count, and a countdown bar. The simulator receives its **RGB pixels**, not tile numbers or odds. The frame never shows this wallet's balance, its own past picks or its results; those enter only as the explicit reinforcement stimulus below.

3,335 mapped R1–R6 cells receive linear-sRGB luminance; 811 mapped R8 cells receive blue/green proxies. Sample locations are inferred from contacts with column-annotated visual cells, using overlapping left/right viewports. Unmapped receptors get no invented optical input. Photoreceptors and lamina are graded in real flies; using spikes, RGB channels, saturating current and a 12 mV-equivalent lamina bias is an explicit display adapter, not validated retinal physiology.

Existing R8→aMe12 connections use a net excitatory sign motivated by [Xiao et al., 2023](https://doi.org/10.1038/s41586-023-06681-6); transferring that result to these reconstructed cells and contact-count magnitudes remains an assumption. Tile shading is a warm orange ramp on a light background. On the full graph, Kenyon-cell activity proved knife-edge sensitive to the palette: blue ramps left the network in a near-silent regime (about 10 KC spikes per observation) on some real boards and an active one (about 4,500) on others; the warm ramp drove the active regime on every board probed. That choice was made on KC activity alone, never on game outcomes. Display sensitivity remains a major confound.

By default each round advances **500 ms of neural time** (about 5 s of wall time on four cores), regardless of the roughly 63 s round. That is a deliberately compressed game-to-neural clock, not real-time fly physiology. Eligibility and decay operate in neural seconds.

## How neural spikes become a tile choice

Every neuron whose annotated cell type starts with `DN` (descending neurons; 1,342 cells in this graph) is sorted by body ID and cut into 21 contiguous groups, one per tile. Each group keeps an exponential moving average of its own rate over about 20 observations, a stand-in for adaptation; without it the lowest-ID group fired at 36 Hz on every board and the same ten tiles were chosen every round. The excess is taken relative to that average because the whole network swings between quiet and active regimes, and a global ramp would otherwise reproduce the raw rate ranking for as long as the average lags. Over each observation:

| Neural measurement | Proposal |
| --- | --- |
| Group rate minus its own running average, divided by that average plus 1 Hz, above the median of those relative excesses | Tile selected |
| Largest excess | Always selected |
| Selection outside the configured tile-count bounds | Trimmed or extended by excess rank |

The stake per round is a fixed setting, not a neural quantity. The mapping is arbitrary and pre-registered; the cell identities of every group are written to `provenance.json`. This is an engineered interface, not a discovery of "tile neurons". Persistent network bias becomes persistent tile preference; do not interpret that as insight into a random draw.

The guard can veto a proposal for timing, budget, count or account-state reasons. It cannot replace the tiles or manufacture a strategy. The deploy transaction is built from the neural mask and the fixed stake only.

## What changes with profit and loss

When a round the fly played has settled, its result is the USDC refunded from losing tiles plus the USDC value of any BTC won (and, in live play, the API-reported RUSH token value) minus the amount deployed. A result of at least +0.01 USDC schedules a **200 ms, 20 mV-equivalent** artificial current into all **15 PAM11 (α1)** cells at the next observation. A result of at most −0.01 USDC schedules the same pulse into the **two PPL101 (γ1pedc)** cells. The pulse is binary above the threshold, not proportional to the amount. If several rounds settle before one observation, their results are summed once.

This is feedback about one round of a random draw, not evidence that the chosen tiles caused it. Fees count as a loss, so most rounds are aversive; a hit on a sparsely selected board is a reward. Wallet deposits and claims never become rewards: only settled deployments do.

The candidate memory rule acts on **7,835 existing KC→MBON07/MBON11 edges**. It adapts a baseline-centered anti-Hebbian rate rule from [Huang, Luo et al., 2024](https://doi.org/10.1038/s41586-024-07819-w): recent KC activity followed by dopamine tends to depress eligible connections; the reverse timing can potentiate them. Actual network spikes supply KC/DAN rates in bins of at most 10 ms. No price or profit value directly edits a synaptic weight.

The 1-second eligibility traces, 1,800-second memory decay, 50 ms efficacy filter, gain 0.001 and efficacy bounds of 0.1–2× baseline are declared model choices. The anatomy-derived DAN-to-MBON contact fractions distribute modulation within each compartment. They are not measured dopamine concentrations or receptor kinetics.

The PAM11/MBON07 compartment is motivated by [Ichinose et al., 2015](https://elifesciences.org/articles/10719). Applying one rule to both α1 and γ1pedc compartments in this male reconstruction is **our unvalidated extension**, not a replication of either paper. Real fly dopamine can have context-dependent effects. “Profit dopamine” and “loss dopamine” are engineered assignments. Pain receptors, subjective pain, pleasure and consciousness are not modeled or measured.

## What would count as learning

The implementation can demonstrate that sensory input reaches memory cells, that selected dopamine cells spike, and that temporal pairing changes eligible synapses. Those are mechanism checks. Even when weights change, useful credit assignment through the fixed trade decoder is unproven.

To claim learned trading behavior requires held-out chronological market replay, independent starts, frozen-weight and shuffled-reinforcement controls, fees/slippage, equal budgets, retention, and loss of benefit after resetting learned weights. Compare to cash and simple exposure baselines as well: rising crypto prices alone can make any buyer look skilled. Avoid selecting a lucky run or tuning on the test period.

The SatRush draw is an on-chain random tile; the fee structure makes every deploy negative in expectation (see [game](game.md)). No readout of a random draw can be profitable on average, and this repository makes no such claim. **No profitable learning, strategy improvement, biological replication, or live-funded performance has been demonstrated by this repository’s tests.** See [validation](validation.md) for the narrower checks actually performed.
