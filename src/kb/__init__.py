"""
Knowledge Base (KB) module for trading bot.
Provides persistent memory for AI decisions, luck/skill analysis, and RAG retrieval.

Mirrors planning-with-files methodology:
1. Create Plan First - Never trade without a trade plan
2. The 2-Action Rule - Update KB after every 2 trades
3. Read Before Decide - Check KB before each trade
4. Update After Act - Log outcome immediately
5. Log ALL Errors - Track every failure with attempts
6. Never Repeat Failures - 3-strike protocol

Statistical Analysis (KS/AD Tests):
- Kolmogorov-Smirnov: Tests decision quality against expected distribution
- Anderson-Darling: Detects tail events (luck/outliers)
- Four Quadrants: Q1(skill+luck), Q2(lucky), Q3(unlucky), Q4(neither)

Signal Denoising (Gaussian Noise + Pareto Trend Separation):
Stock prices are NOT purely Gaussian - they exhibit:
- Gaussian noise: Random day-to-day fluctuations (noise)
- Pareto/heavy-tailed trends: Large moves representing real market shifts (signal)

Denoising Methods:
- WaveletDenoiser: Haar wavelet decomposition with soft thresholding
- EMDDenoiser: Empirical Mode Decomposition into IMFs
- HybridDenoiser: Combined approach for robust trend extraction

After Weeding Out Luck:
- LEARNINGS = Q1 + Q3 (correct decisions regardless of luck outcome) -> REPEAT
- ERRORS = Q2 + Q4 (incorrect decisions regardless of luck outcome) -> AVOID
"""

from .writer import KBWriter
from .reader import KBReader
from .analyzer import DecisionAnalyzer
from .trade_planner import TradePlanner, TradePlan, TradeError
from .strategies import StrategyReader, Strategy
from .manager import cleanup_kb, get_kb_state, log_kb_state
from .luck_statistics import (
    LuckStatisticsAnalyzer,
    StatisticalAnalysis,
    Quadrant,
    DecisionCategory,
    get_category_from_quadrant,
    create_quadrant_summary,
    format_quadrant_report,
    # Denoising classes (Gaussian noise + Pareto trend separation)
    DenoiseResult,
    WaveletDenoiser,
    EMDDenoiser,
    HybridDenoiser
)

__all__ = [
    'KBWriter',
    'KBReader',
    'DecisionAnalyzer',
    'TradePlanner',
    'TradePlan',
    'TradeError',
    'StrategyReader',
    'Strategy',
    'LuckStatisticsAnalyzer',
    'StatisticalAnalysis',
    'Quadrant',
    'DecisionCategory',
    'get_category_from_quadrant',
    'create_quadrant_summary',
    'format_quadrant_report',
    # Denoising
    'DenoiseResult',
    'WaveletDenoiser',
    'EMDDenoiser',
    'HybridDenoiser',
    # KB Management
    'cleanup_kb',
    'get_kb_state',
    'log_kb_state',
]
