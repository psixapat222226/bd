from sqlalchemy import create_engine, text
engine = create_engine("postgresql+psycopg2://postgres:root@localhost:5432/university?sslmode=prefer", future=True)
with engine.connect() as c:
    print(c.execute(text("select 1")).scalar())
