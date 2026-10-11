<?php
/**
 * Plugin Name: Yoast REST Meta (automation)
 * Description: Exposes Yoast SEO title/description/focus keyword as writable REST meta
 *              so the bulk publisher can set them via POST /wp-json/wp/v2/posts { meta: {...} }.
 *
 * Drop into wp-content/mu-plugins/. Yoast's built-in REST output (yoast_head_json) is
 * read-only; underscore-prefixed meta keys are "protected" and need an explicit
 * auth_callback to be writable over REST.
 */

add_action( 'init', function () {
    $keys = array( '_yoast_wpseo_title', '_yoast_wpseo_metadesc', '_yoast_wpseo_focuskw' );

    foreach ( $keys as $key ) {
        register_post_meta( 'post', $key, array(
            'type'              => 'string',
            'single'            => true,
            'show_in_rest'      => true,
            'sanitize_callback' => 'sanitize_text_field',
            'auth_callback'     => function () {
                return current_user_can( 'edit_posts' );
            },
        ) );
    }
} );
