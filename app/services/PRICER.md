# FareLock Pricing Methodology

Technical description of how FareLocker values an American put on a flight
fare. Implementation lives in `curves.py`, `vol.py`, and `pricer.py`.

## Product

A **FareLock** is insurance against a fare *decline*. The buyer pays a fee
up front. Strike \(K\) is the fare at purchase \(S_0\). While the lock is
open they may exercise for a capped put payoff:

\[
\text{payout} = \min\bigl(\max(K - S, 0),\; 0.5\,K\bigr)
\]

Exercise is **American** (any time until departure / expiry), so closed-form
Black–Scholes is not appropriate. We use a Cox–Ross–Rubinstein (CRR)
binomial lattice.

Sell window is at most ~30 days to departure (\(d_0 \le 30\)).

---

## One-sentence model

> Model the predictable fare lifecycle and divide it out; price what’s left
> as an American put on a near-stationary residual; charge a loading for
> risk that cannot be hedged.

Pipeline:

```text
observations
    → route curve f(d, dow, hol)
    → flight premium δ
    → residual X = S / f_flight
    → bucketed σ(X) with shrinkage
    → CRR American put on X (rescaled to $ via f)
    → fee = (1 + λ) · V_model
```

---

## 1. Route term structure \(f(d, \mathrm{dow}, \mathrm{hol})\)

Raw fares trend as departure approaches (inventory / yield management).
That drift is **not** what option math should treat as noise.

### Index

For each route, build a cheapest-fare index: one row per `departure_date`
(minimum observed `price`). Fit on \(\ln S\).

### OLS specification

\[
\ln S_i = \alpha + s(d_i) + \beta_{\mathrm{dow}(i)} + \gamma\cdot\mathrm{holiday}_i + \varepsilon_i
\]

- \(s(d)\): step function over days-to-departure buckets  
  `[(1,3), (4,7), (8,14), (15,21), (22,30), (31,45), (46,60)]`  
  with bucket `[1,3]` absorbed into the intercept.
- \(\mathrm{dow}\): weekday dummies; **Wednesday** is the baseline.
- \(\mathrm{holiday}\): 0/1 flag (currently often 0 in ingest).

Coefficients are stored on `CurveFit.params`. The dollar curve is

\[
f(d,\mathrm{dow},\mathrm{hol}) = \exp\bigl(\hat\alpha + \hat s(d) + \hat\beta_{\mathrm{dow}} + \hat\gamma\cdot\mathrm{hol}\bigr)
\]

with \(d\) clamped to \([1, 60]\).

`refit_all_routes` skips a route if the index has fewer than 30 rows
(previous fit retained).

---

## 2. Flight-level premium \(\delta\)

Flights on a route share lifecycle *shape* but differ in *level*
(morning nonstop vs red-eye, carrier mix, etc.).

\[
\delta_{\mathrm{flight}} = \mathrm{median}_i \ln\Bigl(\frac{S_i}{f(d_i,\mathrm{dow}_i,\mathrm{hol}_i)}\Bigr)
\]

over recent observations for that `flight_no` (minimum observation count
required; thinner flights omitted → \(\delta = 0\) at read time).

\[
f_{\mathrm{flight}}(d,\mathrm{dow},\mathrm{hol},\mathrm{flight})
  = f(d,\mathrm{dow},\mathrm{hol})\cdot e^{\delta_{\mathrm{flight}}}
\]

Stored on `CurveFit.flight_premia` as `{flight_no: δ}`.

---

## 3. Detrended residual \(X\)

\[
X = \frac{S}{f_{\mathrm{flight}}}
\]

Interpretation: is this fare rich or cheap relative to its lifecycle norm?
By construction \(X\) mean-reverts around 1 if the curve is well calibrated.
Option dynamics are specified on \(X\), not on raw \(S\) (which is
non-stationary by design).

---

## 4. Volatility of \(X\)

### Returns

For each series `(flight_no, departure_date)`, sort by `queried_at` and form
log returns \(\Delta \ln X\). Each return is tagged with the **ending**
observation’s \(d\) for bucket assignment.

### Buckets and shrinkage

Sample standard deviation \(\hat\sigma\) per \(d\)-bucket. Shrink toward
prior \(\sigma_0 = 0.35\) (`settings.vol_prior`):

\[
\bar\sigma = w\,\hat\sigma + (1-w)\,\sigma_0,
\quad w = \frac{n}{n + \kappa},\quad \kappa = 20
\]

Empty / thin buckets (\(n < 2\)) use \(\bar\sigma = \sigma_0\), \(w = 0\).
Persisted on `VolFit`.

### Quote-time \(\sigma\)

`quote_lock` averages \(\bar\sigma\) over the remaining path
\(d \in \{d_0, d_0-1, \ldots, 1\}\) and passes a **single** CRR step vol.
(Local-vol lattice is deferred.)

**Caveat:** scrape cadence is often ~30 minutes, while the lattice uses
\(\Delta t = 1\) day. Treating scrape-step \(\sigma\) as daily \(\sigma\)
is a known approximation; rescale (e.g. \(\sigma_{\mathrm{day}} \approx
\sigma_{30\mathrm{m}}\sqrt{48}\)) only if fees look systematically too small.

---

## 5. CRR American put on \(X\)

Implemented in `american_put_crr`.

### State

- Spot residual: \(X_0 = S_0 / f_{\mathrm{flight}}(d_0, \ldots)\)
- Strike: \(K = S_0\)
- Horizon: \(N = T = d_0\) daily steps
- Curve schedule: \(f_i = f_{\mathrm{flight}}(\max(d_0 - i,\, 1), \ldots)\)  
  for \(i = 0,\ldots,N\) (dow / holiday frozen at purchase)

### Tree (on \(X\))

\[
u = e^{\sigma\sqrt{\Delta t}},\quad d = 1/u,\quad \Delta t = 1
\]

With **interest rate \(r = 0\)** (no discounting of leisure cashflows in v1),
the risk-neutral probability that keeps \(X\) a multiplicative martingale is

\[
p^* = \frac{1 - d}{u - d},\quad q^* = 1 - p^*
\]

Node \((i, j)\) (\(j\) up-moves):

\[
X_{i,j} = X_0\, u^{j}\, d^{i-j},
\quad
S_{i,j} = X_{i,j}\cdot f_i
\]

### Intrinsic and early exercise

\[
\mathrm{intrinsic}_{i,j}
  = \min\bigl(\max(K - S_{i,j}, 0),\; 0.5\,K\bigr)
\]

\[
V_{i,j} = \max\bigl(\mathrm{intrinsic}_{i,j},\;
  p^* V_{i+1,j+1} + q^* V_{i+1,j}\bigr)
\]

Special cases:

- \(N = 0\): return spot intrinsic.
- \(\sigma = 0\): single path in \(X\); American value is the max intrinsic
  along \(\{X_0 f_i\}\) (avoids \(u = d = 1\) degeneracy).

Model value \(V_{\mathrm{model}} = V_{0,0}\) (dollars).

---

## 6. Fee / loading

\[
\mathrm{fee} = (1 + \lambda)\, V_{\mathrm{model}},
\quad \lambda = 0.30\ \text{(`settings.base_lambda`)}
\]

\(\lambda\) covers unhedgeable airline / basis / model risk. Inventory
skew on \(\lambda\) as book exposure grows is **not** in v1.

`quote_lock` returns a `Quote` with `V_model`, `fee`, `lam`, `sigma_used`,
`K`, `T_days`, `d0`, `X0`.

---

## Code map

| Piece | Module |
|---|---|
| Curve OLS, \(\delta\), \(f\), \(f_{\mathrm{flight}}\) | `app/services/curves.py` |
| \(X\), returns, bucket \(\sigma\), shrink | `app/services/vol.py` |
| CRR + `quote_lock` | `app/services/pricer.py` |
| Economics knobs | `app/config.py` (`base_lambda`, `vol_prior`) |
| Persistence | `CurveFit`, `VolFit`, `Lock` in `app/models.py` |

---

## Assumptions and limitations (v1)

1. **\(r = 0\)** — no discounting inside the lattice.
2. **Constant \(\sigma\)** on the tree (path-averaged bucket vols).
3. **Frozen dow/holiday** along the path; only \(d\) declines.
4. **Scrape \(\Delta t\) vs daily lattice** — possible under/overstatement of fee.
5. **Curve quality** needs enough distinct departure dates; `< 30` index
   rows → refit skipped.
6. **\(\delta\)** absorbs level differences including carrier mix, not only
   schedule effects.
7. **Payout cap** \(0.5K\) is contractual risk control, not a market quote.

---

## Sanity checks

- On-curve ATM (\(X_0 \approx 1\), flat \(f\), small \(\sigma\)): small but
  positive \(V\); fee \(\approx 1.3\,V\).
- Higher \(\sigma\) ⇒ higher \(V\).
- \(0 \le V_{\mathrm{model}} \le 0.5\,K\).
- Median \(X\) after fit should sit near 1; \(\sigma\) typically rises as
  \(d \to 0\).

Unit tests: `tests/test_curves.py`, `tests/test_vol.py`, `tests/test_pricer.py`.
