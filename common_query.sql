-- Get last hour of bars for SPY
SELECT * FROM bars 
WHERE symbol='SPY' AND ts > NOW() - INTERVAL '1 hour'
ORDER BY ts DESC;

-- Get all signals from today
SELECT * FROM signals 
WHERE DATE(ts_signal) = CURRENT_DATE
ORDER BY ts_signal DESC;

-- Check if signal already exists (15-min cooldown)
SELECT * FROM signals 
WHERE symbol='SPY' 
  AND signal_type='hidden_bullish_divergence'
  AND ts_signal > NOW() - INTERVAL '15 minutes';

-- Performance tracking: signals that led to price increase
SELECT s.*, 
       (SELECT close FROM bars WHERE symbol=s.symbol AND ts > s.ts_signal LIMIT 1) as next_price
FROM signals s
WHERE s.ts_signal > NOW() - INTERVAL '7 days';