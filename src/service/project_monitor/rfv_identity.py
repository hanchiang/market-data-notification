"""The one place the 98% Morpho credit is written down, and the check on it.

`rfv()` does not sum its three components at face value. It credits the Morpho
vault position at 98%:

    rfv() = liquidUsdg + 0.98 x morphoAssets + polRfv

exact in wei in all 133 backfilled epochs, blocks 12,557,107 to 50,439,017.
Evidence, and the neighbouring models it rules out (97.99%, 98.01%, the haircut
on `liquidUsdg`, the haircut on the whole sum -- zero matching epochs each):
MARKET-DATA/docs/traces/2026-08-31-netnet-rfv-morpho-haircut-trace.md.

**The coefficient is observed, never read from the contract.** Nothing here asks
the chain what the credit is; 98% is what the chain has done for 133 consecutive
epochs. So every consumer of it also runs `check_rfv_identity` on the sample it
is about to use, and says so loudly when the equality breaks -- a protocol
re-rating its own vault position would otherwise pass unnoticed while the
residual quietly mis-modelled every deposit after it.
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping, Optional

RFV = 'Treasury.rfv'
LIQUID_USDG = 'Treasury.liquidUsdg'
MORPHO_ASSETS = 'Treasury.morphoAssets'
POL_RFV = 'Treasury.polRfv'
IDENTITY_READINGS = (RFV, LIQUID_USDG, MORPHO_ASSETS, POL_RFV)

# As a fraction of the position, in the numerator/denominator form the wei
# comparison needs: scaling the equality by 100 keeps it in integers, so it is
# tested on exactly the values the chain returned rather than on a rounded
# division of them.
MORPHO_CREDIT_NUMERATOR = 98
MORPHO_CREDIT_DENOMINATOR = 100
MORPHO_CREDIT = Decimal(MORPHO_CREDIT_NUMERATOR) / Decimal(MORPHO_CREDIT_DENOMINATOR)
# What a deposit loses on the way in: value moves from a component credited at
# 100% to one credited at 98%.
MORPHO_DEPOSIT_HAIRCUT = Decimal(1) - MORPHO_CREDIT

IDENTITY_OK = 'ok'
IDENTITY_BROKEN = 'broken'
IDENTITY_INCOMPLETE = 'incomplete'

IDENTITY_EXPRESSION = 'rfv() = liquidUsdg + 0.98 x morphoAssets + polRfv'


@dataclass(frozen=True)
class RfvIdentity:
    state: str
    # rfv() minus the modelled sum, in wei. `None` when a component is missing:
    # an unread component is a gap in the sample, not a broken identity, and
    # reporting it as a break would cry wolf on every failed RPC read.
    diff_wei: Optional[Decimal] = None

    @property
    def holds(self) -> bool:
        return self.state == IDENTITY_OK


def check_rfv_identity(wei: Mapping[str, Optional[int]]) -> RfvIdentity:
    """Check the identity on one sample's four Treasury readings, in wei.

    `wei` maps each reading name to its integer value, or to `None` when the
    read is missing or did not return `ok`.
    """
    values = [wei.get(name) for name in IDENTITY_READINGS]
    if any(value is None for value in values):
        return RfvIdentity(state=IDENTITY_INCOMPLETE)
    rfv, liquid, morpho, pol = (int(value) for value in values)
    scaled_diff = (
        rfv * MORPHO_CREDIT_DENOMINATOR
        - liquid * MORPHO_CREDIT_DENOMINATOR
        - morpho * MORPHO_CREDIT_NUMERATOR
        - pol * MORPHO_CREDIT_DENOMINATOR
    )
    return RfvIdentity(
        state=IDENTITY_OK if scaled_diff == 0 else IDENTITY_BROKEN,
        diff_wei=Decimal(scaled_diff) / MORPHO_CREDIT_DENOMINATOR,
    )
