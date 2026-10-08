package com.pip.rules.kafka;

import com.pip.rules.app.Json;
import com.pip.rules.domain.StoreState;
import org.apache.kafka.common.errors.SerializationException;
import org.apache.kafka.common.serialization.Deserializer;
import org.apache.kafka.common.serialization.Serde;
import org.apache.kafka.common.serialization.Serdes;
import org.apache.kafka.common.serialization.Serializer;

/** JSON serde for the per-(store, run) state. Changelog topics carry the same format. */
public final class StoreStateSerde {
    private StoreStateSerde() {
    }

    public static Serde<StoreState> create() {
        Serializer<StoreState> ser = (topic, data) -> {
            if (data == null) return null;
            try {
                return Json.MAPPER.writeValueAsBytes(data);
            } catch (Exception ex) {
                throw new SerializationException("cannot serialise store state", ex);
            }
        };
        Deserializer<StoreState> de = (topic, bytes) -> {
            if (bytes == null) return null;
            try {
                return Json.MAPPER.readValue(bytes, StoreState.class);
            } catch (Exception ex) {
                throw new SerializationException("cannot deserialise store state", ex);
            }
        };
        return Serdes.serdeFrom(ser, de);
    }
}
