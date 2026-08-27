"""au_lic: Australian LIC/LIT replica of the UK CEF discount study.

Data layer is ASX-specific (monthly investment-products reports for the
point-in-time universe + announcement API for dividends and NTA
cross-validation); the quantitative engine (signals, portfolio, costs,
performance, deciles) is imported unchanged from uk_cef.
"""
