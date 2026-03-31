/**
 * MCP Feedback Enhanced - Queue Manager Module
 * ============================================
 * 
 * Manages the feedback command queue utilizing localStorage for persistence.
 */

(function() {
    'use strict';

    // Ensure namespace exists
    window.MCPFeedback = window.MCPFeedback || {};
    const Utils = window.MCPFeedback.Utils;

    /**
     * Queue Manager constructor
     */
    function QueueManager(options) {
        options = options || {};
        this.queueKey = 'mcp_feedback_command_queue';
        this.queue = this.loadQueue();
        
        // Callbacks
        this.onQueueChanged = options.onQueueChanged || null;
        this.onSubmitFeedback = options.onSubmitFeedback || null;
    }

    /**
     * Load queue from localStorage
     */
    QueueManager.prototype.loadQueue = function() {
        try {
            const data = localStorage.getItem(this.queueKey);
            return data ? JSON.parse(data) : [];
        } catch (e) {
            console.error('Failed to load queue from localStorage:', e);
            return [];
        }
    };

    /**
     * Save queue to localStorage
     */
    QueueManager.prototype.saveQueue = function() {
        try {
            localStorage.setItem(this.queueKey, JSON.stringify(this.queue));
            if (this.onQueueChanged) {
                this.onQueueChanged(this.queue);
            }
        } catch (e) {
            console.error('Failed to save queue to localStorage:', e);
        }
    };

    /**
     * Add an item to the queue
     */
    QueueManager.prototype.enqueue = function(command) {
        if (!command || !command.trim()) return false;
        
        this.queue.push({
            id: Date.now().toString() + '_' + Math.random().toString(36).substr(2, 9),
            text: command.trim(),
            timestamp: Date.now()
        });
        
        this.saveQueue();
        return true;
    };

    /**
     * Remove an item from the queue by ID
     */
    QueueManager.prototype.removeItem = function(id) {
        const initialLength = this.queue.length;
        this.queue = this.queue.filter(function(item) {
            return item.id !== id;
        });
        
        if (this.queue.length !== initialLength) {
            this.saveQueue();
            return true;
        }
        return false;
    };

    /**
     * Clear the entire queue
     */
    QueueManager.prototype.clear = function() {
        this.queue = [];
        this.saveQueue();
    };

    /**
     * Get the current queue
     */
    QueueManager.prototype.getQueue = function() {
        return [...this.queue]; // Return a copy
    };

    /**
     * Check if queue is empty
     */
    QueueManager.prototype.isEmpty = function() {
        return this.queue.length === 0;
    };

    /**
     * Pop the first item and submit it automatically if conditions are met
     */
    QueueManager.prototype.popAndSubmitIfReady = function() {
        if (this.isEmpty()) {
            return false;
        }

        const nextCommand = this.queue.shift();
        this.saveQueue();

        console.log('🔄 Auto-submitting queued command:', nextCommand.text);
        
        // Notify user via toast
        Utils.showMessage('自動送出佇列中的反饋', Utils.CONSTANTS.MESSAGE_INFO);
        
        // Trigger the submit feedback callback
        if (this.onSubmitFeedback) {
            // Need a slight delay to allow UI to transition to active state
            setTimeout(() => {
                this.onSubmitFeedback(nextCommand.text);
            }, 500);
        }
        
        return true;
    };

    // Add QueueManager to namespace
    window.MCPFeedback.QueueManager = QueueManager;

    console.log('✅ QueueManager module loaded');

})();
