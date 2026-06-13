
## 20260604_153524 — ❌ FAIL
**Risk Score:** 10/10
**Violations:** SENTINEL parse error: 429 RESOURCE_EXHAUSTED. {'error': {'code': 429, 'message': 'You exceeded your current quota, please check your plan and billing details. For more information on this error, head to: https://ai.google.dev/gemini-api/docs/rate-limits. To monitor your current usage, head to: https://ai.dev/rate-limit. \n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 0, model: gemini-2.0-flash\n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_requests, limit: 0, model: gemini-2.0-flash\n* Quota exceeded for metric: generativelanguage.googleapis.com/generate_content_free_tier_input_token_count, limit: 0, model: gemini-2.0-flash\nPlease retry in 37.177564433s.', 'status': 'RESOURCE_EXHAUSTED', 'details': [{'@type': 'type.googleapis.com/google.rpc.Help', 'links': [{'description': 'Learn more about Gemini API quotas', 'url': 'https://ai.google.dev/gemini-api/docs/rate-limits'}]}, {'@type': 'type.googleapis.com/google.rpc.QuotaFailure', 'violations': [{'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerDayPerProjectPerModel-FreeTier', 'quotaDimensions': {'model': 'gemini-2.0-flash', 'location': 'global'}}, {'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_requests', 'quotaId': 'GenerateRequestsPerMinutePerProjectPerModel-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.0-flash'}}, {'quotaMetric': 'generativelanguage.googleapis.com/generate_content_free_tier_input_token_count', 'quotaId': 'GenerateContentInputTokensPerModelPerMinute-FreeTier', 'quotaDimensions': {'location': 'global', 'model': 'gemini-2.0-flash'}}]}, {'@type': 'type.googleapis.com/google.rpc.RetryInfo', 'retryDelay': '37s'}]}}
**Notes:** Audit failed to parse — treat as FAIL for safety

## 20260604_154419 — ✅ PASS
**Risk Score:** 0/10
**Notes:** SENTINEL operational, no code changes to review

## 20260604_161810 — ❌ FAIL | Risk: 8/10
**Context:** Self-test — full CB6 audit
**Checklist:** symbol_rules: PASS | risk_limits: PASS | execution_safety: PASS | signal_integrity: FAIL | ml_safety: PASS
**Violations (6):**
  - 🔴 H4 bias violation on FTMO: USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - 🔴 H4 bias violation on FTMO: XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
  - 🔴 H4 bias violation on FTMO: XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - 🔴 H4 bias violation on FTMO: XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
  - 🔴 H4 bias violation on GFT_5K: USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - 🔴 H4 bias violation on GFT_5K: XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
**Warnings:**
  - ⚠️ FTMO: H4 violation — XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
  - ⚠️ FTMO: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - ⚠️ Multiple H4 bias violations detected across FTMO and GFT_5K accounts
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ GFT_5K: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - ⚠️ FTMO: H4 violation — XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
  - ⚠️ XAUUSD still disabled on GFT accounts as per permanent ban
**H4 VIOLATIONS DETECTED IN STATE FILES**
**Deploy approved:** False
**Notes:** Multiple H4 bias violations detected across FTMO and GFT_5K accounts. Immediate attention required to rectify these issues before deployment.

## 20260605_083823 — ❌ FAIL | Risk: 8/10
**Context:** Self-test — full CB6 audit
**Checklist:** symbol_rules: PASS | risk_limits: PASS | execution_safety: PASS | signal_integrity: FAIL | ml_safety: PASS
**Violations (3):**
  - 🔴 H4 bias check not enforced on FTMO (multiple H4 violations detected)
  - 🔴 H4 bias check not enforced on GFT 5K (multiple H4 violations detected)
  - 🔴 XAUUSD not permanently disabled on FTMO (H4 violation detected on 2026-06-03)
**Warnings:**
  - ⚠️ FTMO: H4 violation — XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - ⚠️ Risk of significant losses due to H4 bias check not being enforced
  - ⚠️ FTMO: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ GFT_5K: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ FTMO: H4 violation — XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
  - ⚠️ Multiple H4 violations detected across FTMO and GFT 5K
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-06-05 00:45
**H4 VIOLATIONS DETECTED IN STATE FILES**
**Deploy approved:** False
**Notes:** Multiple H4 violations detected across FTMO and GFT 5K. XAUUSD not permanently disabled on FTMO. Risk of significant losses due to H4 bias check not being enforced.

## 20260606_080003 — ❌ FAIL | Risk: 8/10
**Context:** Self-test — full CB6 audit
**Checklist:** symbol_rules: PASS | risk_limits: PASS | execution_safety: PASS | signal_integrity: FAIL | ml_safety: PASS
**Violations (1):**
  - 🔴 H4 bias check bypassed or ignored in multiple instances across FTMO and GFT_5K accounts
**Warnings:**
  - ⚠️ GFT_5K: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - ⚠️ FTMO: H4 violation — XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
  - ⚠️ FTMO: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ Multiple H4 violations detected in state files, indicating potential strategy or execution issues
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-06-05 00:45
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - ⚠️ No code diff provided for audit, relying on general system knowledge
  - ⚠️ FTMO: H4 violation — XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
**H4 VIOLATIONS DETECTED IN STATE FILES**
**Deploy approved:** False
**Notes:** Audit failed due to repeated H4 bias violations across multiple accounts, indicating a need for strategy review and potential adjustments to execution logic.

## 20260607_111609 — ❌ FAIL | Risk: 8/10
**Context:** Self-test — full CB6 audit
**Checklist:** symbol_rules: FAIL | risk_limits: PASS | execution_safety: PASS | signal_integrity: FAIL | ml_safety: PASS
**Violations (2):**
  - 🔴 XAUUSD trading detected on FTMO despite permanent ban
  - 🔴 Multiple H4 violations detected across FTMO and GFT_5K accounts
**Warnings:**
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-06-05 00:45
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ GBPUSD still enabled despite low 33% WR
  - ⚠️ GFT_5K: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - ⚠️ No explicit approval for new symbol additions
  - ⚠️ FTMO: H4 violation — XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
  - ⚠️ FTMO: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ FTMO: H4 violation — XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
**H4 VIOLATIONS DETECTED IN STATE FILES**
**Deploy approved:** False
**Notes:** Multiple H4 violations and potential XAUUSD trading detected. Immediate review and correction required.

## 20260608_083513 — ❌ FAIL | Risk: 8/10
**Context:** Self-test — full CB6 audit
**Checklist:** symbol_rules: PASS | risk_limits: PASS | execution_safety: PASS | signal_integrity: FAIL | ml_safety: PASS
**Violations (2):**
  - 🔴 H4 bias check bypassed or ignored in multiple instances across FTMO and GFT_5K accounts
  - 🔴 Multiple H4 violations detected in state file issues, indicating signal integrity concerns
**Warnings:**
  - ⚠️ FTMO: H4 violation — XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
  - ⚠️ GFT_5K: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - ⚠️ FTMO: H4 violation — XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ GFT_5K account also shows H4 violations, warranting a review of trading logic and discipline
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-06-05 00:45
  - ⚠️ FTMO: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - ⚠️ FTMO account exhibits frequent H4 violations, suggesting potential strategy or execution issues
**H4 VIOLATIONS DETECTED IN STATE FILES**
**Deploy approved:** False
**Notes:** Audit reveals significant signal integrity concerns due to repeated H4 violations across multiple accounts, necessitating a thorough review and adjustment of trading strategies and discipline to ensure alignment with H4 bias checks.

## 20260609_091005 — ❌ FAIL | Risk: 8/10
**Context:** Self-test — full CB6 audit
**Checklist:** symbol_rules: PASS | risk_limits: PASS | execution_safety: PASS | signal_integrity: FAIL | ml_safety: PASS
**Violations (1):**
  - 🔴 H4 bias check bypassed or ignored in multiple instances across FTMO and GFT_5K accounts
**Warnings:**
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - ⚠️ FTMO: H4 violation — XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
  - ⚠️ FTMO: H4 violation — XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-06-05 00:45
  - ⚠️ GFT_5K: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - ⚠️ No code diff provided for audit, relying on general system knowledge
  - ⚠️ Multiple H4 violations detected in state files, indicating potential strategy or execution issues
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ FTMO: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
**H4 VIOLATIONS DETECTED IN STATE FILES**
**Deploy approved:** False
**Notes:** Audit reveals multiple H4 violations across accounts, indicating potential strategy or execution issues. Symbol rules, risk limits, execution safety, and ML safety appear to be in compliance, but signal integrity is compromised due to H4 bias check bypass or ignorance.

## 20260610_084512 — ❌ FAIL | Risk: 8/10
**Context:** Self-test — full CB6 audit
**Checklist:** symbol_rules: PASS | risk_limits: PASS | execution_safety: PASS | signal_integrity: FAIL | ml_safety: PASS
**Violations (1):**
  - 🔴 H4 bias check bypassed or ignored in multiple instances across FTMO and GFT_5K accounts
**Warnings:**
  - ⚠️ No code diff provided for audit, relying on general system knowledge
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ FTMO: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ GFT_5K: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - ⚠️ FTMO: H4 violation — XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-06-05 00:45
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - ⚠️ Multiple H4 violations detected in state files, indicating potential strategy or execution issues
  - ⚠️ FTMO: H4 violation — XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
**H4 VIOLATIONS DETECTED IN STATE FILES**
**Deploy approved:** False
**Notes:** Audit failed due to repeated H4 violations across multiple accounts, indicating a need for strategy review and potential adjustments to execution logic.

## 20260611_082153 — ❌ FAIL | Risk: 8/10
**Context:** Self-test — full CB6 audit
**Checklist:** symbol_rules: PASS | risk_limits: PASS | execution_safety: PASS | signal_integrity: FAIL | ml_safety: PASS
**Violations (1):**
  - 🔴 H4 bias check bypassed or ignored in multiple instances across FTMO and GFT_5K accounts
**Warnings:**
  - ⚠️ No code diff provided for audit, relying on general system knowledge
  - ⚠️ FTMO: H4 violation — XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
  - ⚠️ GFT_5K: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - ⚠️ FTMO: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ Multiple H4 violations detected in state files, indicating potential strategy or execution issues
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-06-05 00:45
  - ⚠️ FTMO: H4 violation — XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
**H4 VIOLATIONS DETECTED IN STATE FILES**
**Deploy approved:** False
**Notes:** Audit reveals multiple H4 violations across accounts, indicating potential issues with strategy or execution. Symbol rules, risk limits, execution safety, and ML safety appear to be in order, but signal integrity is compromised due to H4 bias check issues.

## 20260612_091104 — ❌ FAIL | Risk: 8/10
**Context:** Self-test — full CB6 audit
**Checklist:** symbol_rules: FAIL | risk_limits: PASS | execution_safety: PASS | signal_integrity: FAIL | ml_safety: PASS
**Violations (2):**
  - 🔴 XAUUSD traded on FTMO despite permanent ban
  - 🔴 H4 bias check bypassed or ignored in multiple instances
**Warnings:**
  - ⚠️ FTMO: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ No new symbols added but review recommended for optimal performance
  - ⚠️ GFT_5K: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - ⚠️ FTMO: H4 violation — XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
  - ⚠️ GBPUSD still enabled despite low 33% WR
  - ⚠️ FTMO: H4 violation — XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-06-05 00:45
**H4 VIOLATIONS DETECTED IN STATE FILES**
**Deploy approved:** False
**Notes:** Multiple H4 violations detected across FTMO and GFT_5K accounts. XAUUSD traded on FTMO despite being permanently banned. Immediate attention required to rectify these issues and prevent further risk exposure.

## 20260613_153755 — ❌ FAIL | Risk: 8/10
**Context:** Self-test — full CB6 audit
**Checklist:** symbol_rules: PASS | risk_limits: PASS | execution_safety: FAIL | signal_integrity: FAIL | ml_safety: PASS
**Violations (2):**
  - 🔴 H4 violation detected in FTMO and GFT_5K accounts
  - 🔴 Multiple H4 violations detected across different dates and symbols
**Warnings:**
  - ⚠️ FTMO account has multiple H4 violations
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-06-05 00:45
  - ⚠️ FTMO: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-12 13:27
  - ⚠️ FTMO: H4 violation — XAUUSD BULLISH with H4=BEARISH on 2026-06-03 17:00
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-12 13:30
  - ⚠️ GFT_5K: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 14:00
  - ⚠️ FTMO: H4 violation — XAGUSD BULLISH with H4=BEARISH on 2026-05-26 16:45
  - ⚠️ FTMO: H4 violation — USOIL BULLISH with H4=BEARISH on 2026-05-25 12:45
  - ⚠️ XAGUSD and XAUUSD symbols have repeated H4 violations
  - ⚠️ GFT_5K account has multiple H4 violations
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-01 13:15
  - ⚠️ GFT_5K: H4 violation — XAGUSD BEARISH with H4=BULLISH on 2026-06-12 12:51
**H4 VIOLATIONS DETECTED IN STATE FILES**
**Deploy approved:** False
**Notes:** Multiple H4 violations detected across FTMO and GFT_5K accounts. Execution safety and signal integrity checks failed due to these violations.
