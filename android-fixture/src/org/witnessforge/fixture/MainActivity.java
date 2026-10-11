package org.witnessforge.fixture;

import android.app.Activity;
import android.os.Bundle;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.widget.TextView;

/** Owned test app, not the Telephony provider. No SMS or network permissions. */
public final class MainActivity extends Activity {
    private SQLiteDatabase database;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        TextView text = new TextView(this);
        text.setPadding(32, 64, 32, 32);
        try {
            deleteFile("ready.txt");
            deleteDatabase("mmssms.db");
            database = openOrCreateDatabase("mmssms.db", MODE_PRIVATE, null);
            database.enableWriteAheadLogging();
            try (Cursor configuration = database.rawQuery("PRAGMA wal_autocheckpoint=0", null)) {
                configuration.moveToFirst();
            }
            database.execSQL("CREATE TABLE sms (_id INTEGER PRIMARY KEY, thread_id INTEGER, address TEXT, date INTEGER, date_sent INTEGER DEFAULT 0, read INTEGER DEFAULT 0, status INTEGER DEFAULT -1, type INTEGER, subject TEXT, body TEXT, service_center TEXT, sub_id INTEGER DEFAULT -1)");
            database.execSQL("INSERT INTO sms (_id,thread_id,address,date,date_sent,read,type,body,sub_id) VALUES (1,7,'+15550001001',1700000000123,1700000000000,1,1,'Owned fixture: incoming',1)");
            try (Cursor checkpoint = database.rawQuery("PRAGMA wal_checkpoint(TRUNCATE)", null)) {
                checkpoint.moveToFirst();
            }
            // These committed records remain in WAL while this connection stays open.
            database.execSQL("INSERT INTO sms (_id,thread_id,address,date,read,type,body,sub_id) VALUES (2,7,'+15550001001',1700000001123,1,2,'Owned fixture: sent from WAL',1)");
            database.execSQL("INSERT INTO sms (_id,thread_id,address,date,read,type,body,sub_id) VALUES (3,8,NULL,1700000002123,0,3,'Draft unicode: café ☕',2)");
            try (java.io.FileOutputStream ready = openFileOutput("ready.txt", MODE_PRIVATE)) {
                ready.write("fixture-v1-ready".getBytes(java.nio.charset.StandardCharsets.UTF_8));
            }
            text.setText("WitnessForge owned Android fixture\n\n3 synthetic SMS-shaped records created using Android SQLite.\n\nThis is not the system SMS provider and contains no real messages.\n\nKeep this screen open until acquisition. The acquisition command force-stops this app and preserves the WAL.");
        } catch (Exception failure) {
            text.setText("Fixture setup failed: " + failure.toString());
            throw new RuntimeException(failure);
        }
        setContentView(text);
    }
}
