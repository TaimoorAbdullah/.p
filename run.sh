#!/usr/bin/env bash
cd "$(dirname "$0")"
python3 -m pip install -r backend/requirements.txt -r frontend/requirements.txt
(cd backend && python3 -m uvicorn app.main:app --port 8000) &
sleep 3; cd frontend && python3 -m streamlit run streamlit_app.py
