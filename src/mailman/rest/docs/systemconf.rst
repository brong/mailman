====================
System configuration
====================

The entire system configuration is available through the REST API.  You can
get a list of all defined sections.

    >>> from mailman.testing.documentation import dump_json
    >>> dump_json('http://localhost:9001/3.0/system/configuration')
    http_etag: ...
    sections: ['ARC', 'antispam', 'archiver.mail_archive', 'archiver.master', ...
    self_link: http://localhost:9001/3.0/system/configuration

You can also get all the values for a particular section, such as the
``[mailman]`` section...

    >>> dump_json('http://localhost:9001/3.0/system/configuration/mailman')
    anonymous_list_keep_headers: ^x-mailman- ^x-content-filtered-by: ^x-topics:
    ^x-ack: ^x-beenthere: ^x-list-administrivia: ^x-spam-
    cache_life: 7d
    check_max_size_on_filtered_message: no
    default_language: en
    dsn_lifetime: 1d
    email_commands_max_lines: 10
    filter_report: no
    filtered_messages_are_preservable: no
    hold_digest: no
    html_to_plain_text_command: /usr/bin/lynx -dump $filename
    http_etag: ...
    layout: testing
    listname_chars: [-_.0-9a-z]
    masthead_threshold: 4
    moderator_request_life: 180d
    noreply_address: noreply
    pending_request_life: 3d
    post_hook:
    pre_hook:
    run_tasks_every: 1h
    self_link: http://localhost:9001/3.0/system/configuration/mailman
    sender_headers: from from_ reply-to sender
    site_owner: noreply@example.com

...or the ``[dmarc]`` section (or any other).

    >>> dump_json('http://localhost:9001/3.0/system/configuration/dmarc')
    cache_lifetime: 7d
    http_etag: ...
    org_domain_data_url: https://publicsuffix.org/list/public_suffix_list.dat
    resolver_lifetime: 5s
    resolver_timeout: 3s
    self_link: http://localhost:9001/3.0/system/configuration/dmarc

Dotted section names work too, for example, to get the French language
settings section.

    >>> dump_json('http://localhost:9001/3.0/system/configuration/language.fr')
    charset: iso-8859-1
    description: French
    enabled: yes
    http_etag: ...
    self_link: http://localhost:9001/3.0/system/configuration/language.fr
