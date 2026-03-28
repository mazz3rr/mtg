"""

    mtg.data.db
    ~~~~~~~~~~~
    Handle database operations.

    @author: mazz3rr

"""
from sqlalchemy import create_engine, event, exists, func, select
from sqlalchemy.orm import Session

from mtg.constants import APP_DIR
from mtg.data.models import Base, Deck, Decklist


# https://x.com/i/grok/share/1c53708fb9b148ba885ce61556618b5e
def exists_in_table(session: Session, model: type[Base], **filters) -> bool:
    """Check if a record exists using simple equality filters.

    Example:
        exists_in_table(session, User, email="test@example.com")
        exists_in_table(session, User, email="test@example.com", active=True)
    """
    stmt = select(
        exists().where(
            select(model).filter_by(**filters).exists()
        )
    )
    return session.scalar(stmt) is True


@event.listens_for(Deck, "after_delete")
def delete_orphan_decklist(mapper, connection, target: Deck) -> None:
    """Delete Decklist if it has no more referencing Decks after a Deck is deleted.
    """
    if target.decklist_id is None:
        return

    session = Session.object_session(target)
    if session is None:
        return  # object was already detached, or we're in a weird state

    # count remaining Decks that still point to this Decklist
    remaining_count = session.scalar(
        select(func.count())
        .where(Deck.decklist_id == target.decklist_id)
    )

    if remaining_count == 0:
        # load the Decklist (it should still exist at this point)
        decklist = session.get(Decklist, target.decklist_id)
        if decklist:
            session.delete(decklist)
            # note: we do NOT call session.commit() here
            # the delete will be part of the current transaction/flush


# init database
DB_PATH = APP_DIR / "scraped_data.db"
ENGINE = create_engine(f"sqlite:///{DB_PATH}")
Base.metadata.create_all(ENGINE)
