from typing import Callable, Tuple


class ArbitrageOptimizer:
    """
    optimizes the trade/borrow size for an arbitrage route.
    uses golden-section search on the strictly quasi-concave net profit function.
    """

    # golden ratio constant phi = (1 + sqrt(5)) / 2 approx 1.6180339887
    # rescaling factor r = (sqrt(5) - 1) / 2 approx 0.6180339887
    INV_PHI = (5**0.5 - 1.0) / 2.0
    INV_PHI_SQ = (3.0 - 5**0.5) / 2.0

    @classmethod
    def optimize_borrow_amount(
        cls,
        profit_func: Callable[[int], int],
        min_amount: int,
        max_amount: int,
        tolerance: int = 1000,
        max_iterations: int = 25,
    ) -> Tuple[int, int]:
        """
        finds the input amount x in [min_amount, max_amount] that maximizes profit_func(x).

        args:
            profit_func: callable accepting integer amount_in and returning integer net profit.
            min_amount: lower bound for search (in token base units).
            max_amount: upper bound for search (in token base units).
            tolerance: stopping precision threshold in token base units.
            max_iterations: safety limit for iterations.

        returns:
            tuple of (optimal_amount_in, max_profit).
        """
        if min_amount >= max_amount:
            p = profit_func(min_amount)
            return min_amount, p

        a = min_amount
        b = max_amount

        h = b - a
        if h <= tolerance:
            p_a = profit_func(a)
            p_b = profit_func(b)
            return (a, p_a) if p_a >= p_b else (b, p_b)

        # evaluate initial interior test points
        c = a + int(cls.INV_PHI_SQ * h)
        d = a + int(cls.INV_PHI * h)

        yc = profit_func(c)
        yd = profit_func(d)

        for _ in range(max_iterations):
            if (b - a) <= tolerance:
                break

            if yc > yd:
                b = d
                d = c
                yd = yc
                h = b - a
                c = a + int(cls.INV_PHI_SQ * h)
                yc = profit_func(c)
            else:
                a = c
                c = d
                yc = yd
                h = b - a
                d = a + int(cls.INV_PHI * h)
                yd = profit_func(d)

        # check final candidates
        candidates = [a, b, c, d]
        best_amount = a
        best_profit = profit_func(a)

        for cand in candidates[1:]:
            p = profit_func(cand)
            if p > best_profit:
                best_profit = p
                best_amount = cand

        return best_amount, best_profit


if __name__ == "__main__":
    # test the optimizer with a simulated parabolic net profit function:
    # profit(x) peaks at x = 1250, zero at x = 2500
    def mock_profit_func(x: int) -> int:
        return -(x - 1250) ** 2 + 1_000_000

    opt_x, max_p = ArbitrageOptimizer.optimize_borrow_amount(
        mock_profit_func, min_amount=100, max_amount=5000, tolerance=1
    )
    print("--- optimizer test ---")
    print(f"optimal borrow amount: {opt_x} (expected ~1250)")
    print(f"max profit: {max_p}")
