//! Compile with the pinned Horizon byte channel and the prepared ingress files.
#![allow(dead_code, unused_imports)]

extern crate self as horizon_mod_runtime;
extern crate self as horizon_protocol;
extern crate self as mod_api;

pub mod byte_channel;
pub mod ingress;

#[derive(Debug, PartialEq, Eq)]
pub enum PayloadContextError {
    Backpressure,
    Disconnected,
}

pub mod payload_negotiation {
    #[derive(Clone, Copy, PartialEq, Eq)]
    pub enum Phase {
        Configuration,
        Play,
    }
}

use payload_negotiation::Phase;
use std::{sync::Arc, sync::mpsc, thread, time::Duration};

mod net {
    pub use crate::ingress;
    pub enum Event {
        Chunk,
        RawPayload(Box<crate::raw_payloads::Incoming>),
    }
}

mod lan {
    pub enum NetEvent {
        Packet,
        RawPayload(Box<crate::raw_payloads::Delivery>),
    }
}

mod raw_payloads {
    use crate::Phase;
    use std::sync::Arc;

    #[derive(Clone)]
    pub struct Endpoint;
    pub struct Epoch {
        pub phase: Phase,
    }
    pub enum Body {
        Payload(()),
        Work(()),
    }
    pub struct Incoming {
        pub epoch: Arc<Epoch>,
        pub body: Body,
    }
    pub struct Delivery {
        pub conn: Option<u32>,
        pub endpoint: Endpoint,
        pub incoming: Incoming,
    }

    pub mod main_queue;
}

use net::Event;
use raw_payloads::main_queue::MainQueue;
use raw_payloads::{Body, Epoch, Incoming};

const LIMIT: Duration = Duration::from_secs(3);
const PRESSURE_SAMPLE: Duration = Duration::from_millis(50);

fn incoming(phase: Phase, body: Body) -> Incoming {
    Incoming {
        epoch: Arc::new(Epoch { phase }),
        body,
    }
}

fn client_queue() -> (ingress::Producer<Event>, ingress::Consumer<Event>) {
    let (producer, consumer) =
        ingress::channel(byte_channel::message_overhead::<Event>(), |_| 0).unwrap();
    assert!(producer.send(Event::Chunk).is_ok());
    (producer, consumer)
}

#[test]
fn play_payload_waits_for_chunk_credit_and_publishes_in_order() {
    let (producer, consumer) = client_queue();
    let (reserved_tx, reserved_rx) = mpsc::channel();
    let (publish_tx, publish_rx) = mpsc::channel();
    let network_reader = thread::spawn(move || {
        let queue = MainQueue::Client(producer);
        let permit = queue
            .prepare_incoming(incoming(Phase::Play, Body::Payload(())))
            .unwrap();
        reserved_tx.send(()).unwrap();
        publish_rx.recv_timeout(LIMIT).unwrap();
        assert!(permit.publish().is_ok());
    });
    let (chunk, retained_credit) = consumer.try_recv_owned().unwrap().into_parts();
    assert!(matches!(chunk, Event::Chunk));
    assert_eq!(
        reserved_rx.recv_timeout(PRESSURE_SAMPLE),
        Err(mpsc::RecvTimeoutError::Timeout)
    );
    drop(retained_credit);
    reserved_rx.recv_timeout(LIMIT).unwrap();
    assert!(matches!(
        consumer.try_recv_owned(),
        Err(mpsc::TryRecvError::Empty)
    ));
    publish_tx.send(()).unwrap();
    network_reader.join().unwrap();
    let (event, _) = consumer.try_recv_owned().unwrap().into_parts();
    assert!(matches!(event, Event::RawPayload(_)));
    assert_eq!(
        consumer.stats().peak_reserved_bytes,
        byte_channel::message_overhead::<Event>()
    );
}

#[test]
fn closing_the_client_receiver_releases_a_waiting_network_reader() {
    let (producer, consumer) = client_queue();
    let (result_tx, result_rx) = mpsc::channel();
    let network_reader = thread::spawn(move || {
        let queue = MainQueue::Client(producer);
        let result = queue
            .prepare_incoming(incoming(Phase::Play, Body::Payload(())))
            .err();
        result_tx.send(result).unwrap();
    });
    assert_eq!(
        result_rx.recv_timeout(PRESSURE_SAMPLE),
        Err(mpsc::RecvTimeoutError::Timeout)
    );
    drop(consumer);
    assert_eq!(
        result_rx.recv_timeout(LIMIT).unwrap(),
        Some(PayloadContextError::Disconnected)
    );
    network_reader.join().unwrap();
}

#[test]
fn configuration_and_borrowed_handler_work_still_refuse_a_full_queue() {
    let (producer, _consumer) = client_queue();
    let queue = MainQueue::Client(producer);
    assert_eq!(
        queue
            .prepare_incoming(incoming(Phase::Configuration, Body::Payload(())))
            .err(),
        Some(PayloadContextError::Backpressure),
    );
    assert_eq!(
        queue
            .prepare_incoming(incoming(Phase::Play, Body::Work(())))
            .err(),
        Some(PayloadContextError::Backpressure),
    );
    assert_eq!(
        queue
            .prepare(incoming(Phase::Play, Body::Payload(())))
            .err(),
        Some(PayloadContextError::Backpressure),
    );
}

#[test]
fn pooled_server_handoff_still_refuses_a_full_queue() {
    let (events, _consumer) =
        ingress::channel(byte_channel::message_overhead::<lan::NetEvent>(), |_| 0).unwrap();
    assert!(events.send(lan::NetEvent::Packet).is_ok());
    let queue = MainQueue::Server {
        events,
        endpoint: raw_payloads::Endpoint,
        conn: Some(17),
    };
    assert_eq!(
        queue
            .prepare_incoming(incoming(Phase::Play, Body::Payload(())))
            .err(),
        Some(PayloadContextError::Backpressure),
    );
}
