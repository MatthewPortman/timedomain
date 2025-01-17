import time
import psycopg2
import sqlite3
import numpy

#get array of yyyymmdds to loop through
#getUnprocessedDates.py
def getUnprocessedDates(prod = "daily"):
    start_time = time.time()
    # Connect to desi.db with POSTGRES to get latest observed and unprocessed yyyymmdd
    f = open('/global/cfs/cdirs/desi/science/td/secrets/desi_pg.txt') #what is more valid?
    file = f.read()
    db_name, db_user, db_pwd, db_host = file.split()
    conn = psycopg2.connect(dbname=db_name, user=db_user, password=db_pwd, host=db_host)
    cur = conn.cursor()
    cur.execute("""SELECT DISTINCT yyyymmdd from fibermap_daily WHERE yyyymmdd > 20210604""") #most recent, remove in future
    desi_arr = cur.fetchall()
    cur.close()
    conn.close()
    
    # Open to transients_search.db for latest processed yyyymmdd to do comparison with unprocessed
    if (prod == "daily"):
        filename_conn = "/global/cfs/cdirs/desi/science/td/daily-search/transients_search.db"
        conn = sqlite3.connect(filename_conn)
        trans_arr = conn.execute("""SELECT DISTINCT yyyymmdd from unprocessed_exposures""").fetchall()
        conn.close()
    elif (prod == "everest"):
        filename_conn = "/global/cfs/cdirs/desi/science/td/daily-search/transients_search.db"
        conn = sqlite3.connect(filename_conn)
        trans_arr = conn.execute("""SELECT DISTINCT yyyymmdd from processed_everest""").fetchall()
        conn.close()
    
    # Open to transients_search.db for latest processed yyyymmdd to do comparison with empty nights
    # filename_conn = "/global/cfs/cdirs/desi/science/td/daily-search/transients_search.db"
    # conn = sqlite3.connect(filename_conn)
    # empty_dates_arr = conn.execute("""SELECT DISTINCT yyyymmdd from empty_dates""").fetchall()
    # conn.close()

    # Compare yyyymmdd from fibermap_daily with unprocessed_exposures, retaining those in fibermap_daily and not in unprocessed_daily
    night_arr = numpy.setdiff1d(desi_arr, trans_arr)
    #Remove empty nights
    # night_arr = numpy.setdiff1d(night_arr, empty_dates_arr)

    print('len(night_arr): ' + str(len(night_arr)))
    print("--- get unprocessed dates took:  %s seconds ---" % (time.time() - start_time))
    
    return(night_arr)

def hasNothingToProcess(tps,group_tid,group_tp,group_night):
    hasNothing=True
    for tid, tp, night in zip(group_tid,group_tp,group_night):
        # if this RA/DEC is not in thie tile_petal combination than skip
        if tp[0] not in tps:
            continue
        if len(night) > 1:
            hasNothing=False
            return hasNothing
    return hasNothing

def processed(tid, tileid, date):
    filename_conn = "/global/cfs/cdirs/desi/science/td/daily-search/transients_search.db"
    try:
        sqliteConnection = sqlite3.connect(filename_conn)
        cursor = sqliteConnection.cursor()
        sqlite_insert_query = """INSERT INTO daily_processed (TARGETID, TILEID, YYYYMMDD) VALUES({0},{1},{2})""".format(tid, tileid, date)
        count = cursor.execute(sqlite_insert_query)
        sqliteConnection.commit()
        cursor.close()
    except sqlite3.Error as error:
        print("Failed to insert data into sqlite table", error)
    finally:
        if sqliteConnection:
            sqliteConnection.close()
            # print("The SQLite connection is closed")
            
