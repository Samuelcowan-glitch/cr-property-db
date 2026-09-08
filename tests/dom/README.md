# Browser tests

These check behaviour that only exists in a browser — whether a button
actually submits its form, whether the page keeps its scroll position.
Every Python test drives the server directly, where that behaviour does not
exist at all, which is how a submit-cancelling guard shipped and stopped
Download PDF working for days.

They need jsdom, installed once outside the repository:

    mkdir -p ~/.local/cr-domtest && cd ~/.local/cr-domtest
    npm install jsdom

Then run them from here:

    sh tests/dom/run.sh
