# utils/chart_renderer.py — ICT chart screenshots + trade replay
# Uses matplotlib to render annotated OHLC charts and send them via Telegram.
import os
import sys
import io
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from utils.logger import logger

try:
    import matplotlib
    matplotlib.use('Agg')   # non-GUI backend, safe for server
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle
    HAS_MPL = True
except Exception as e:
    logger.warning(f"matplotlib not available: {e}")
    HAS_MPL = False


# ─── #26 SETUP SCREENSHOT ────────────────────────────────────────────────────

def render_setup_chart(df, setup, save_path=None):
    """
    Render an ICT setup chart with annotations:
      - Candles (last 80)
      - Sweep low/high (red dot)
      - MSS level (orange line)
      - FVG zone (purple band)
      - OTE zone (green band)
      - Entry, SL, T1/T2/T3 (horizontal dashed)
    Returns PNG bytes (or saves to file if save_path given).
    """
    if not HAS_MPL or df is None or len(df) < 10:
        return None

    try:
        sig    = setup.get('entry_signal', {})
        symbol = setup.get('symbol', '').replace('NSE:', '').replace('-EQ', '')
        direction = setup.get('direction', 'BUY')

        window = df.tail(80).reset_index(drop=True)
        n = len(window)

        fig, ax = plt.subplots(figsize=(12, 6), facecolor='#0d1117')
        ax.set_facecolor('#0d1117')

        # Plot candles
        for i, row in window.iterrows():
            color = '#00B050' if row['close'] >= row['open'] else '#FF4444'
            ax.plot([i, i], [row['low'], row['high']], color=color, linewidth=0.8)
            body = abs(row['close'] - row['open'])
            ax.add_patch(Rectangle(
                (i - 0.3, min(row['open'], row['close'])),
                0.6, body, color=color, alpha=0.85
            ))

        # FVG zone
        if sig.get('fvg_low') and sig.get('fvg_high'):
            ax.axhspan(sig['fvg_low'], sig['fvg_high'],
                       color='purple', alpha=0.15, label='FVG')

        # OTE zone
        if sig.get('ote_low') and sig.get('ote_high'):
            ax.axhspan(sig['ote_low'], sig['ote_high'],
                       color='green', alpha=0.10, label='OTE')

        # Key horizontal levels
        levels = [
            (sig.get('entry'),     '#58a6ff', 'Entry'),
            (sig.get('stop_loss'), '#FF4444', 'SL'),
            (sig.get('target1'),   '#00B050', 'T1'),
            (sig.get('target2'),   '#00B050', 'T2'),
            (sig.get('target3'),   '#00B050', 'T3'),
        ]
        for level, color, label in levels:
            if level:
                ax.axhline(level, color=color, linestyle='--', linewidth=1, alpha=0.7)
                ax.text(n + 0.5, level, f'{label} {level}',
                        color=color, fontsize=9, va='center')

        # Sweep marker
        sweep_low = sig.get('sweep_low')
        if sweep_low and direction == 'BUY':
            sweep_idx = window['low'].idxmin()
            ax.scatter(sweep_idx, sweep_low, color='red', s=120, zorder=5,
                       marker='v', label='Sweep')

        # FRVP POC line
        frvp = setup.get('frvp', {}) or {}
        if frvp.get('poc'):
            ax.axhline(frvp['poc'], color='yellow', linestyle=':',
                       linewidth=1.5, alpha=0.8)
            ax.text(0, frvp['poc'], f"POC {frvp['poc']}",
                    color='yellow', fontsize=8)

        # Title with score + psychology
        score = setup.get('confluence', '?')
        psy   = setup.get('psychology', {}) or {}
        title = (
            f"{symbol} {direction} | Score {score}/10 | "
            f"{psy.get('phase', 'NEUTRAL')} | {psy.get('crowd', 'NEUTRAL')}"
        )
        ax.set_title(title, color='white', fontsize=12, pad=10)
        ax.tick_params(colors='#8b949e')
        for spine in ax.spines.values():
            spine.set_color('#30363d')
        ax.grid(True, alpha=0.1)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=100, facecolor=fig.get_facecolor())
            plt.close()
            return save_path

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, facecolor=fig.get_facecolor())
        plt.close()
        buf.seek(0)
        return buf.read()

    except Exception as e:
        logger.error(f"Chart render error: {e}")
        return None


# ─── #27 TRADE REPLAY ────────────────────────────────────────────────────────

def render_trade_replay(df, trade, save_path=None):
    """
    Render a closed trade's full lifecycle:
      - Entry candle (blue marker)
      - Exit candle (red/green based on result)
      - Targets hit (green checkmarks at T1/T2/T3 levels)
      - SL line (red dashed)
      - Highlight the in-trade window
    """
    if not HAS_MPL or df is None or len(df) < 10:
        return None

    try:
        symbol = trade['symbol'].replace('NSE:', '').replace('-EQ', '')
        direction = trade.get('direction', 'BUY')

        # Find entry/exit indices in the df by timestamp
        entry_time = trade.get('entry_time', '')
        exit_time  = trade.get('exit_time', '')
        entry_idx, exit_idx = None, None

        for i, row in df.iterrows():
            ts = str(row.get('timestamp', ''))
            if entry_idx is None and ts >= entry_time:
                entry_idx = i
            if exit_time and ts >= exit_time:
                exit_idx = i
                break

        if entry_idx is None:
            entry_idx = max(0, len(df) - 30)
        if exit_idx is None:
            exit_idx = len(df) - 1

        # Show 10 candles before entry through exit
        start = max(0, entry_idx - 10)
        end   = min(len(df), exit_idx + 5)
        window = df.iloc[start:end].reset_index(drop=True)
        rel_entry = entry_idx - start
        rel_exit  = exit_idx - start

        fig, ax = plt.subplots(figsize=(12, 6), facecolor='#0d1117')
        ax.set_facecolor('#0d1117')

        # Candles
        for i, row in window.iterrows():
            color = '#00B050' if row['close'] >= row['open'] else '#FF4444'
            ax.plot([i, i], [row['low'], row['high']], color=color, linewidth=0.8)
            body = abs(row['close'] - row['open'])
            ax.add_patch(Rectangle(
                (i - 0.3, min(row['open'], row['close'])),
                0.6, body, color=color, alpha=0.85
            ))

        # Highlight in-trade window
        ax.axvspan(rel_entry, rel_exit, color='#58a6ff', alpha=0.06)

        # Levels
        levels = [
            (trade.get('entry_price'), '#58a6ff', 'Entry'),
            (trade.get('stop_loss'),   '#FF4444', 'SL'),
            (trade.get('target1'),     '#00B050', 'T1'),
            (trade.get('target2'),     '#00B050', 'T2'),
            (trade.get('target3'),     '#00B050', 'T3'),
        ]
        for level, color, label in levels:
            if level:
                ax.axhline(level, color=color, linestyle='--', linewidth=1, alpha=0.7)
                ax.text(len(window) + 0.5, level, f'{label} {level}',
                        color=color, fontsize=9, va='center')

        # Entry/exit markers
        ax.scatter(rel_entry, trade.get('entry_price'), color='#58a6ff', s=150,
                   marker='^' if direction == 'BUY' else 'v', zorder=10, label='Entry')
        ax.scatter(rel_exit, trade.get('exit_price', trade.get('entry_price')),
                   color='#FF4444' if trade.get('pnl', 0) < 0 else '#00B050',
                   s=150, marker='X', zorder=10, label='Exit')

        # Targets-hit checkmarks
        for tgt in trade.get('targets_hit', []):
            tgt_price = trade.get('target1' if tgt == 'T1' else 'target2' if tgt == 'T2' else 'target3')
            if tgt_price:
                ax.scatter(rel_exit, tgt_price, color='#00B050', s=80,
                           marker='*', zorder=11)

        result = "WIN" if trade.get('pnl', 0) > 0 else "LOSS"
        result_col = '#00B050' if result == 'WIN' else '#FF4444'
        title = (
            f"{symbol} {direction} REPLAY | {result} Rs {trade.get('pnl', 0):.0f} | "
            f"Targets: {','.join(trade.get('targets_hit', [])) or 'none'} | "
            f"Status: {trade.get('status', '?')}"
        )
        ax.set_title(title, color=result_col, fontsize=12, pad=10)
        ax.tick_params(colors='#8b949e')
        for spine in ax.spines.values():
            spine.set_color('#30363d')
        ax.grid(True, alpha=0.1)

        plt.tight_layout()

        if save_path:
            plt.savefig(save_path, dpi=100, facecolor=fig.get_facecolor())
            plt.close()
            return save_path

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=100, facecolor=fig.get_facecolor())
        plt.close()
        buf.seek(0)
        return buf.read()

    except Exception as e:
        logger.error(f"Replay render error: {e}")
        return None
