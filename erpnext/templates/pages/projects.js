frappe.ready(function() {

	$('.task-status-switch').on('click', function() {
		var $btn = $(this);
		if($btn.attr('data-status')==='Open') {
			reload_products('completed', 'task', $btn);
		} else {
			reload_products('open', 'task', $btn);
		}
	})


	$('.issue-status-switch').on('click', function() {
		var $btn = $(this);
		if($btn.attr('data-status')==='Open') {
			reload_products('completed', 'issue', $btn);
		} else {
			reload_products('open', 'issue', $btn);
		}
	})

	var start = 10;
	$(".more-tasks").click(function() {
		more_products('task', true);
	});

	$(".more-issues").click(function() {
		more_products('issue', true);
	});

	$(".more-timelogs").click(function() {
		more_products('timelog', false);
	});

	$(".more-timelines").click(function() {
		more_products('timeline', false);
	});

	$(".file-size").each(function() {
		$(this).text(frappe.form.formatters.FileSize($(this).text()));
	});


	var reload_products = function(product_status, product, $btn) {
		$.ajax({
			method: "GET",
			url: "/",
			dataType: "json",
			data: {
				cmd: "erpnext.templates.pages.projects.get_"+ product +"_html",
				project: '{{ doc.name }}',
				product_status: product_status,
			},
			success: function(data) {
				if(typeof data.message == 'undefined') {
					$('.project-'+ product).html("No "+ product_status +" "+ product);
					$(".more-"+ product).toggle(false);
				}
				$('.project-'+ product).html(data.message);
				$(".more-"+ product).toggle(true);

				// update status
				if(product_status==='open') {
					$btn.html(__('Show Completed')).attr('data-status', 'Open');
				} else {
					$btn.html(__('Show Open')).attr('data-status', 'Completed');
				}
			}
		});

	}

	var more_products = function(product, product_status){
		if(product_status) {
			var product_status = $('.project-'+ product +'-section .btn-group .bold').hasClass('btn-completed-'+ product)
				? 'completed' : 'open';
		}
		$.ajax({
			method: "GET",
			url: "/",
			dataType: "json",
			data: {
				cmd: "erpnext.templates.pages.projects.get_"+ product +"_html",
				project: '{{ doc.name }}',
				start: start,
				product_status: product_status,
			},
			success: function(data) {

				$(data.message).appendTo('.project-'+ product);
				if(typeof data.message == 'undefined') {
					$(".more-"+ product).toggle(false);
				}
				start = start+10;
			}
		});
	}

	var close_product = function(product, product_name){
		var args = {
			project: '{{ doc.name }}',
			product_name: product_name,
		}
		frappe.call({
			btn: this,
			type: "POST",
			method: "erpnext.templates.pages.projects.set_"+ product +"_status",
			args: args,
			callback: function(r) {
				if(r.exc) {
					if(r._server_messages)
						frappe.msgprint(r._server_messages);
				} else {
					$(this).remove();
				}
			}
		})
		return false;
	}
});
