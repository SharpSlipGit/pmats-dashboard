# PMATS Dashboard

Live trading dashboard for the Prediction Market Algorithmic Trading System.

Built with Streamlit. Connects to Kalshi API for real-time balance and position data.

## Deploy to Streamlit Cloud

1. Push this repo to GitHub
2. Go to [share.streamlit.io](https://share.streamlit.io)
3. Connect your GitHub repo
4. Set main file to `pmats_dashboard.py`
5. Add your secrets in the Streamlit Cloud Secrets manager (see `secrets_example.toml`)

## Local Development

```bash
pip install -r requirements.txt
streamlit run pmats_dashboard.py
```
