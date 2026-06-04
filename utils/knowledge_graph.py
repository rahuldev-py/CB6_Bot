"""
Knowledge Graph Engine — CB6 Quantum Phase 5
Macro cause-and-effect reasoning for CB6.

Provides:
  impact_of(node)       → what else moves when this node changes
  explain(from, to)     → causal path between two nodes
  regime_context()      → current macro state summary
  trade_context(symbol) → relevant macro factors for a trading symbol

Usage:
  from utils.knowledge_graph import KnowledgeGraph
  kg = KnowledgeGraph()
  kg.impact_of("OIL_PRICE")
  kg.explain("FED_RATE", "NIFTY50")
  kg.trade_context("XAUUSD")
  python -m utils.knowledge_graph      # interactive CLI
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

GRAPH_PATH = Path(__file__).parent.parent / "data" / "macro_graph.json"


@dataclass
class MacroEdge:
    source:    str
    target:    str
    direction: str     # POSITIVE | NEGATIVE
    strength:  float   # 0.1 – 1.0
    lag_days:  int
    notes:     str     = ""


@dataclass
class ImpactResult:
    node:       str
    direction:  str    # POSITIVE | NEGATIVE
    strength:   float
    lag_days:   int
    path:       list[str]
    depth:      int
    notes:      str = ""

    def strength_label(self) -> str:
        if self.strength >= 0.70: return "STRONG"
        if self.strength >= 0.40: return "MODERATE"
        return "WEAK"

    def direction_arrow(self) -> str:
        return "↑" if self.direction == "POSITIVE" else "↓"


class KnowledgeGraph:
    """Lightweight directed macro knowledge graph."""

    def __init__(self, graph_path: str = None):
        path = Path(graph_path) if graph_path else GRAPH_PATH
        with open(path, encoding="utf-8") as f:
            data = json.load(f)

        self._nodes: dict = data.get("nodes", {})
        self._raw_edges: list = data.get("edges", [])

        # Build adjacency: node → list of MacroEdge
        self._out_edges: dict[str, list[MacroEdge]] = {}
        self._in_edges:  dict[str, list[MacroEdge]] = {}
        for e in self._raw_edges:
            edge = MacroEdge(
                source=e["from"], target=e["to"],
                direction=e["direction"], strength=e.get("strength", 0.5),
                lag_days=e.get("lag_days", 0), notes=e.get("notes", "")
            )
            self._out_edges.setdefault(e["from"], []).append(edge)
            self._in_edges.setdefault(e["to"],   []).append(edge)

    # ---------------------------------------------------------------------------
    # Core query: what does this node affect?
    # ---------------------------------------------------------------------------

    def impact_of(self, node: str, depth: int = 2,
                  min_strength: float = 0.3) -> list[ImpactResult]:
        """
        BFS traversal: what nodes are affected when `node` changes?
        Handles compound direction (neg × neg = pos).
        Returns list sorted by combined strength (direct first).
        """
        results: list[ImpactResult] = []
        visited = {node}
        queue = [(node, "POSITIVE", 1.0, 0, [node])]  # (current, cumulative_dir, cum_strength, depth, path)

        while queue:
            current, cum_dir, cum_str, d, path = queue.pop(0)
            if d > depth:
                continue
            for edge in self._out_edges.get(current, []):
                if edge.target in visited:
                    continue
                visited.add(edge.target)

                # Compound direction: POSITIVE × NEGATIVE = NEGATIVE
                new_dir = "POSITIVE" if (cum_dir == edge.direction) else "NEGATIVE"
                # Compound strength: decay through chain
                new_str = cum_str * edge.strength

                if new_str < min_strength:
                    continue

                results.append(ImpactResult(
                    node=edge.target,
                    direction=new_dir,
                    strength=new_str,
                    lag_days=edge.lag_days,
                    path=path + [edge.target],
                    depth=d,
                    notes=edge.notes,
                ))

                if d + 1 <= depth:
                    queue.append((edge.target, new_dir, new_str, d + 1, path + [edge.target]))

        return sorted(results, key=lambda r: (-r.strength, r.depth))

    # ---------------------------------------------------------------------------
    # Core query: path between two nodes
    # ---------------------------------------------------------------------------

    def explain(self, from_node: str, to_node: str,
                max_depth: int = 4) -> list[list[str]]:
        """
        Find all causal paths from from_node to to_node.
        Returns list of paths (each path is a list of node names).
        """
        paths = []
        stack = [(from_node, [from_node])]

        while stack:
            current, path = stack.pop()
            if current == to_node:
                paths.append(path)
                continue
            if len(path) > max_depth:
                continue
            for edge in self._out_edges.get(current, []):
                if edge.target not in path:
                    stack.append((edge.target, path + [edge.target]))

        return sorted(paths, key=len)

    def explain_detailed(self, from_node: str, to_node: str) -> list[dict]:
        """
        Like explain() but returns full edge details for each path step.
        """
        paths = self.explain(from_node, to_node)
        result = []
        for path in paths:
            steps = []
            net_direction = "POSITIVE"
            net_strength  = 1.0
            for i in range(len(path) - 1):
                edge = self._get_edge(path[i], path[i + 1])
                if edge:
                    steps.append({
                        "from":      path[i],
                        "to":        path[i + 1],
                        "direction": edge.direction,
                        "strength":  edge.strength,
                        "lag_days":  edge.lag_days,
                        "notes":     edge.notes,
                    })
                    net_direction = "POSITIVE" if (net_direction == edge.direction) else "NEGATIVE"
                    net_strength *= edge.strength

            result.append({
                "path":          path,
                "steps":         steps,
                "net_direction": net_direction,
                "net_strength":  round(net_strength, 3),
                "total_lag":     sum(s["lag_days"] for s in steps),
            })
        return sorted(result, key=lambda x: -x["net_strength"])

    # ---------------------------------------------------------------------------
    # Trade context: given a symbol, what macro factors matter?
    # ---------------------------------------------------------------------------

    def trade_context(self, symbol_or_node: str) -> dict:
        """
        Return relevant macro context for a trading symbol.
        Maps XAUUSD → GOLD, USOIL → OIL_PRICE, NSE:NIFTY50 → NIFTY50, etc.
        """
        # Map trading symbol → graph node
        symbol_map = {
            "XAUUSD":              "GOLD",
            "XAGUSD":              "SILVER",
            "USOIL":               "OIL_PRICE",
            "EURUSD":              "EURUSD",
            "NSE:NIFTY50-INDEX":   "NIFTY50",
            "NSE:NIFTYBANK-INDEX": "NIFTYBANK",
            "NSE:FINNIFTY-INDEX":  "FINNIFTY",
            "NSE:MIDCPNIFTY-INDEX":"MIDCPNIFTY",
            "NIFTY":               "NIFTY50",
            "BANKNIFTY":           "NIFTYBANK",
        }
        node = symbol_map.get(symbol_or_node, symbol_or_node)

        # Direct drivers: nodes that cause this node to move (in-edges)
        drivers = []
        for edge in self._in_edges.get(node, []):
            drivers.append({
                "driver":    edge.source,
                "label":     self._nodes.get(edge.source, {}).get("label", edge.source),
                "direction": edge.direction,
                "strength":  edge.strength,
                "lag_days":  edge.lag_days,
                "notes":     edge.notes,
            })
        drivers.sort(key=lambda d: -d["strength"])

        # What this node affects (out-edges)
        impacts = []
        for edge in self._out_edges.get(node, []):
            impacts.append({
                "target":    edge.target,
                "label":     self._nodes.get(edge.target, {}).get("label", edge.target),
                "direction": edge.direction,
                "strength":  edge.strength,
            })
        impacts.sort(key=lambda i: -i["strength"])

        return {
            "node":     node,
            "label":    self._nodes.get(node, {}).get("label", node),
            "type":     self._nodes.get(node, {}).get("type", "UNKNOWN"),
            "drivers":  drivers[:5],   # top 5 drivers
            "impacts":  impacts[:5],   # top 5 impacts
        }

    # ---------------------------------------------------------------------------
    # Regime context: given known macro state, what does it mean?
    # ---------------------------------------------------------------------------

    def regime_context(self, active_nodes: list[str] = None) -> dict:
        """
        Given a list of currently "active" macro nodes (rising/high),
        compute the cascading expected impacts across the graph.
        Returns a summary of expected market moves.

        active_nodes: e.g. ["OIL_PRICE", "US_INFLATION"]
        """
        if not active_nodes:
            return {"impacts": [], "note": "No active macro nodes specified"}

        all_impacts: dict[str, list[ImpactResult]] = {}
        for node in active_nodes:
            for impact in self.impact_of(node, depth=3):
                target = impact.node
                if target not in all_impacts:
                    all_impacts[target] = []
                all_impacts[target].append(impact)

        # Aggregate: if same target appears from multiple sources, combine
        summary = []
        for target, impacts in all_impacts.items():
            pos = [i for i in impacts if i.direction == "POSITIVE"]
            neg = [i for i in impacts if i.direction == "NEGATIVE"]
            net_pos_str = sum(i.strength for i in pos)
            net_neg_str = sum(i.strength for i in neg)
            net_dir = "POSITIVE" if net_pos_str >= net_neg_str else "NEGATIVE"
            net_str = abs(net_pos_str - net_neg_str)
            label = self._nodes.get(target, {}).get("label", target)
            summary.append({
                "target":     target,
                "label":      label,
                "direction":  net_dir,
                "strength":   round(net_str, 3),
                "sources":    len(impacts),
                "min_lag":    min(i.lag_days for i in impacts),
            })

        summary.sort(key=lambda x: -x["strength"])
        return {
            "active_nodes": active_nodes,
            "impacts":      summary[:12],
        }

    # ---------------------------------------------------------------------------
    # Node info
    # ---------------------------------------------------------------------------

    def node_info(self, node: str) -> dict:
        return self._nodes.get(node, {})

    def nodes_by_type(self, node_type: str) -> list[str]:
        return [k for k, v in self._nodes.items() if v.get("type") == node_type]

    # ---------------------------------------------------------------------------
    # Private helpers
    # ---------------------------------------------------------------------------

    def _get_edge(self, source: str, target: str) -> Optional[MacroEdge]:
        for edge in self._out_edges.get(source, []):
            if edge.target == target:
                return edge
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_impact(kg: KnowledgeGraph, node: str):
    print(f"\nImpact of {node} rising/increasing:")
    print(f"  {'Target':<22} {'Dir':>4}  {'Strength':<10} {'Lag':>5}d  {'Path'}")
    print(f"  {'─'*22} {'─'*4}  {'─'*9} {'─'*6}  {'─'*30}")
    for r in kg.impact_of(node, depth=2):
        label = kg.node_info(r.node).get("label", r.node)
        arrow = "↑" if r.direction == "POSITIVE" else "↓"
        path_s = " → ".join(r.path[1:])
        print(f"  {r.node:<22} {arrow:>4}  {r.strength_label():<10} {r.lag_days:>5}  {path_s}")


def _print_explain(kg: KnowledgeGraph, from_node: str, to_node: str):
    print(f"\nCausal path: {from_node} → {to_node}")
    paths = kg.explain_detailed(from_node, to_node)
    if not paths:
        print("  No path found.")
        return
    for i, p in enumerate(paths[:3]):
        arrow = "↑" if p["net_direction"] == "POSITIVE" else "↓"
        print(f"\n  Path {i+1}: net={arrow} strength={p['net_strength']:.3f} lag={p['total_lag']}d")
        print(f"  {'─' * 60}")
        for step in p["steps"]:
            d = "↑" if step["direction"] == "POSITIVE" else "↓"
            print(f"    {step['from']:<20} {d} → {step['to']:<20}  "
                  f"({step['strength']:.2f} str, {step['lag_days']}d lag)")
            print(f"       {step['notes']}")


def _print_trade_context(kg: KnowledgeGraph, symbol: str):
    ctx = kg.trade_context(symbol)
    print(f"\nMacro context for {symbol} ({ctx['label']}):")
    print(f"\n  Top drivers (what causes {ctx['node']} to move):")
    for d in ctx["drivers"]:
        arrow = "↑" if d["direction"] == "POSITIVE" else "↓"
        print(f"    {d['driver']:<20} {arrow} str={d['strength']:.2f}  lag={d['lag_days']}d  {d['notes'][:50]}")
    print(f"\n  What {ctx['node']} affects downstream:")
    for imp in ctx["impacts"]:
        arrow = "↑" if imp["direction"] == "POSITIVE" else "↓"
        print(f"    {imp['target']:<20} {arrow} str={imp['strength']:.2f}")


def _demo():
    kg = KnowledgeGraph()
    print("\n" + "═" * 72)
    print(f"{'CB6 QUANTUM — KNOWLEDGE GRAPH':^72}")
    print("═" * 72)

    for node in ["OIL_PRICE", "FED_RATE", "GOLD"]:
        _print_impact(kg, node)

    print()
    for pair in [("FED_RATE", "NIFTY50"), ("OIL_PRICE", "NIFTYBANK"), ("USD_INDEX", "NIFTY_IT")]:
        _print_explain(kg, pair[0], pair[1])

    print()
    for sym in ["XAUUSD", "USOIL", "NSE:NIFTY50-INDEX", "NSE:NIFTYBANK-INDEX"]:
        _print_trade_context(kg, sym)

    print("\n" + "─" * 72)
    print("  Scenario: Oil + US Inflation both rising simultaneously:")
    ctx = kg.regime_context(["OIL_PRICE", "US_INFLATION"])
    print(f"  {'Target':<22} {'Dir':>4}  {'Strength':>9}  {'Sources':>8}  {'MinLag':>7}d")
    print(f"  {'─'*22} {'─'*4}  {'─'*9}  {'─'*8}  {'─'*7}")
    for imp in ctx["impacts"]:
        arrow = "↑" if imp["direction"] == "POSITIVE" else "↓"
        print(f"  {imp['target']:<22} {arrow:>4}  {imp['strength']:>9.3f}  "
              f"{imp['sources']:>8}  {imp['min_lag']:>7}")


if __name__ == "__main__":
    import sys
    args = sys.argv[1:]
    kg = KnowledgeGraph()

    if len(args) == 1:
        _print_impact(kg, args[0].upper())
    elif len(args) == 2:
        _print_explain(kg, args[0].upper(), args[1].upper())
    else:
        _demo()
