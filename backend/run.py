"""Start CampaignFlow (API + UI + background worker).

    python run.py                    # http://127.0.0.1:8000
    CF_SIMULATE=1 python run.py      # also simulate ad-platform delivery every minute
"""
import os

from campaignflow import create_app

app = create_app()

if __name__ == "__main__":
    app.start_worker()
    app.run(host=os.environ.get("HOST", "127.0.0.1"), port=int(os.environ.get("PORT", "8000")),
            threaded=True, use_reloader=False)
