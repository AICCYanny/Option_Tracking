from apps.api.app.db.engine import engine
from apps.api.app.db.models import Base

def init_db():
    print("Creating all tables...")
    Base.metadata.create_all(bind=engine)
    print("Done.")

def reset_db():
    print("Dropping all tables...")
    Base.metadata.drop_all(bind=engine)
    print("Creating all tables...")
    Base.metadata.create_all(bind=engine)
    print("Done.")

if __name__ == "__main__":
    reset_db()